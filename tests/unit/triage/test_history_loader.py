"""Tests for the history loader: backoff, digest checks and rebuilds, with no real waiting."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.unit.triage.history_fakes import (
    DOWN,
    NEW_DIGEST,
    CountingEmbedder,
    FakeDigest,
    open_history,
)
from triage.history_loader import (
    BACKOFF_MAX_S,
    BACKOFF_START_S,
    VERIFY_INTERVAL_S,
    HistoryLoader,
    next_delay,
    start_in_background,
)
from triage.history_store import PersistentHistory


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
