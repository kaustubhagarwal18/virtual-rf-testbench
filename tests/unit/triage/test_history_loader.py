"""Tests for the history loader: backoff, digest checks and rebuilds, with no real waiting."""

import json
import sqlite3
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tests.unit.triage.history_fakes import (
    DATASET_RUNS,
    DOWN,
    NEW_DIGEST,
    CountingEmbedder,
    FakeDigest,
    open_history,
)
from triage.case import CaseError
from triage.embed_cache import EmbeddingCache
from triage.history_db import DatabaseError
from triage.history_loader import (
    BACKOFF_MAX_S,
    BACKOFF_START_S,
    VERIFY_INTERVAL_S,
    HistoryLoader,
    next_delay,
    start_in_background,
)
from triage.history_store import PersistentHistory
from triage.llm import ModelUnavailableError


@dataclass
class ScriptedWait:
    """Records each wait instead of sleeping, runs a step, and stops the loop after a limit.

    Attributes:
        stop: The loader's stop event, set once the limit is reached.
        limit: How many waits before stopping.
        step: Called with the wait's number (from 1); returns whether the wait was woken.
        waits: The seconds of each wait, in order.
    """

    stop: threading.Event
    limit: int
    step: Callable[[int], bool] = lambda _: False
    waits: list[float] = field(default_factory=list)

    def __call__(self, seconds: float) -> bool:
        """Record the wait, run the step, and stop the loop at the limit."""
        self.waits.append(seconds)
        woken = self.step(len(self.waits))
        if len(self.waits) >= self.limit:
            self.stop.set()
        return woken


def run_loader(
    history: PersistentHistory, limit: int, step: Callable[[int], bool] | None = None
) -> list[float]:
    """Run a loader with a scripted wait until the limit; return the waits it made."""
    stop = threading.Event()
    wait = ScriptedWait(stop, limit, step) if step is not None else ScriptedWait(stop, limit)
    HistoryLoader(history, stop=stop, wait=wait).run()
    return wait.waits


@pytest.mark.parametrize(
    ("delay_s", "expected_s"),
    [
        (BACKOFF_START_S, 2 * BACKOFF_START_S),
        (BACKOFF_MAX_S / 2 + 1, BACKOFF_MAX_S),
        (BACKOFF_MAX_S, BACKOFF_MAX_S),
    ],
)
def test_the_retry_delay_doubles_up_to_the_cap(delay_s: float, expected_s: float) -> None:
    """Each retry waits twice as long as the last, never more than the cap."""
    assert next_delay(delay_s) == expected_s


def test_failed_loads_back_off_and_say_why(tmp_path: Path) -> None:
    """With Ollama down and nothing cached, waits go 2, 4, 8 ... 60 s and readiness says why."""
    history = open_history(tmp_path, digest=FakeDigest(down=True))
    waits = run_loader(history, limit=7)
    assert waits == [2.0, 4.0, 8.0, 16.0, 32.0, BACKOFF_MAX_S, BACKOFF_MAX_S]
    reason = history.not_ready_reason()
    assert reason is not None and DOWN in reason


def test_a_changed_digest_found_while_watching_rebuilds_the_index(tmp_path: Path) -> None:
    """A verified index is rechecked every interval; a new digest is rebuilt and served."""
    digest = FakeDigest()
    history = open_history(tmp_path, digest=digest)

    def change_model(number: int) -> bool:
        """Swap the model under the same name before the first check."""
        if number == 1:
            digest.digest = NEW_DIGEST
        return False

    waits = run_loader(history, limit=2, step=change_model)
    assert waits == [VERIFY_INTERVAL_S, VERIFY_INTERVAL_S]
    assert history.status().digest == NEW_DIGEST
    assert history.not_ready_reason() is None


def test_a_stale_index_wakes_the_loader_to_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wake-up while watching goes straight back to loading."""
    history = open_history(tmp_path)
    loads: list[int] = []
    load = history.load

    def counted() -> None:
        """Count each load, then load."""
        loads.append(1)
        load()

    monkeypatch.setattr(history, "load", counted)
    run_loader(history, limit=2, step=lambda number: number == 1)
    expected_loads = 2
    assert len(loads) == expected_loads


def test_an_offline_start_keeps_checking_the_digest_until_ollama_answers(tmp_path: Path) -> None:
    """Loaded from the cache with Ollama down, the digest is retried with backoff, then verified."""
    open_history(tmp_path).load()
    digest = FakeDigest(down=True)
    history = open_history(tmp_path, embedder=CountingEmbedder(down=True), digest=digest)
    second_wait = 2

    def ollama_returns(number: int) -> bool:
        """Bring Ollama back during the second wait."""
        if number == second_wait:
            digest.down = False
        return False

    waits = run_loader(history, limit=3, step=ollama_returns)
    assert waits == [BACKOFF_START_S, 2 * BACKOFF_START_S, VERIFY_INTERVAL_S]
    assert history.status().digest_verified is True


def test_an_unusable_database_is_never_retried(tmp_path: Path) -> None:
    """A file that can't be opened needs a person, so the loader stops at once."""
    (tmp_path / "history.db").write_bytes(b"not a database")
    assert not run_loader(open_history(tmp_path), limit=1)


def test_the_loader_runs_on_a_daemon_thread(tmp_path: Path) -> None:
    """The background thread is named and never keeps the process alive."""
    loader = HistoryLoader(open_history(tmp_path))
    loader.stop.set()
    thread = start_in_background(loader)
    thread.join(timeout=5)
    assert thread.daemon and thread.name == "history-loader"
    assert not thread.is_alive()


@pytest.mark.parametrize(
    "failure",
    [
        ModelUnavailableError(DOWN),
        DatabaseError("the history database history.db failed (disk I/O error)"),
        CaseError("dataset/cable_loss-1: label.json is unusable"),
        json.JSONDecodeError("Expecting value", "<html>", 0),
        ValueError("all the input array dimensions must match"),
        KeyError("cause"),
        sqlite3.OperationalError("database is locked"),
        OSError("No such file or directory"),
    ],
    ids=["ollama", "database", "case", "json", "value", "key", "sqlite", "os"],
)
def test_the_loader_survives_every_expected_failure_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """A load that fails is recorded for readiness, retried after backoff, then served."""
    history = open_history(tmp_path)
    load = history.load
    attempts: list[int] = []

    def fails_once() -> None:
        """Fail the first load with the given error, then load."""
        attempts.append(1)
        if len(attempts) == 1:
            raise failure
        load()

    reasons: list[str | None] = []

    def note_reason(number: int) -> bool:
        """Record readiness during the first wait, after the failed attempt."""
        if number == 1:
            reasons.append(history.not_ready_reason())
        return False

    monkeypatch.setattr(history, "load", fails_once)
    waits = run_loader(history, limit=2, step=note_reason)
    assert waits == [BACKOFF_START_S, VERIFY_INTERVAL_S]
    assert reasons[0] is not None and str(failure)[:20].strip("'") in reasons[0]
    assert history.not_ready_reason() is None


NOT_NUMBERS: Any = ["not", "a", "number"]
"""An embedding a broken model might send: a list, but not of numbers."""


@dataclass
class GarbageEmbedder(CountingEmbedder):
    """Answers with non-numeric embeddings until told to recover, as a broken model might.

    Attributes:
        garbage: While True, every vector is NOT_NUMBERS.
    """

    garbage: bool = True

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return strings instead of numbers while garbage is set."""
        if self.garbage:
            return [NOT_NUMBERS for _ in texts]
        return super().embed(texts)


def test_a_non_numeric_embedding_is_retried_until_the_model_recovers(tmp_path: Path) -> None:
    """Strings for numbers would once have killed the loader; now it waits, says why, and loads."""
    embedder = GarbageEmbedder()
    history = open_history(tmp_path, embedder=embedder)
    reasons: list[str | None] = []

    def recover(number: int) -> bool:
        """Note readiness, then fix the model, during the first wait."""
        if number == 1:
            reasons.append(history.not_ready_reason())
            embedder.garbage = False
        return False

    waits = run_loader(history, limit=2, step=recover)
    assert waits == [BACKOFF_START_S, VERIFY_INTERVAL_S]
    assert reasons[0] is not None and "couldn't be used" in reasons[0]
    assert history.not_ready_reason() is None


def test_a_bad_label_is_reported_by_folder_and_a_fixed_one_loads(tmp_path: Path) -> None:
    """A label missing its cause names the run; once repaired, the next attempt loads."""
    history = open_history(tmp_path)
    label = tmp_path / "dataset" / DATASET_RUNS[0][0] / "label.json"
    good = label.read_text(encoding="utf-8")
    label.write_text("{}", encoding="utf-8")
    reasons: list[str | None] = []

    def repair(number: int) -> bool:
        """Note readiness, then restore the label, during the first wait."""
        if number == 1:
            reasons.append(history.not_ready_reason())
            label.write_text(good, encoding="utf-8")
        return False

    waits = run_loader(history, limit=2, step=repair)
    assert waits == [BACKOFF_START_S, VERIFY_INTERVAL_S]
    assert reasons[0] is not None and DATASET_RUNS[0][0] in reasons[0]
    assert history.not_ready_reason() is None


def test_a_digest_check_that_cant_be_recorded_is_retried_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk while recording a confirmed digest keeps it unverified and tries again."""
    open_history(tmp_path).load()
    digest = FakeDigest(down=True)
    history = open_history(tmp_path, embedder=CountingEmbedder(down=True), digest=digest)
    record = EmbeddingCache.record_digest
    failures: list[int] = []

    def full_once(self: EmbeddingCache, *arguments: Any) -> None:
        """Fail the first recording as a full disk does, then record."""
        if not failures:
            failures.append(1)
            raise DatabaseError("the history database history.db failed (disk is full)")
        record(self, *arguments)

    def ollama_returns(number: int) -> bool:
        """Bring Ollama back during the first wait, with the disk full for one write."""
        if number == 1:
            digest.down = False
            monkeypatch.setattr(EmbeddingCache, "record_digest", full_once)
        return False

    waits = run_loader(history, limit=3, step=ollama_returns)
    assert waits == [BACKOFF_START_S, 2 * BACKOFF_START_S, VERIFY_INTERVAL_S]
    assert failures == [1]
    assert history.digest_verified() is True
    assert history.not_ready_reason() is None
