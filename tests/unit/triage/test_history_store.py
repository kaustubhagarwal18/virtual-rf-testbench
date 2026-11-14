"""Tests for the persistent history: durable writes, duplicates, loading and rebuilding."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.unit.triage.history_fakes import (
    DATASET_RUNS,
    DIGEST,
    NEW_DIGEST,
    CountingEmbedder,
    FakeDigest,
    open_history,
)
from tests.unit.triage.test_retrieval import labelled_run
from triage import history_store
from triage.bench_run import load_case
from triage.case import Case, CaseError
from triage.embed import FakeEmbedder
from triage.embed_cache import EmbeddingCache
from triage.history_db import DatabaseError
from triage.history_store import (
    LOADING_REASON,
    MODEL_CHANGED_REASON,
    STALE_REASON,
    HistoryUnavailableError,
    PersistentHistory,
)
from triage.llm import ModelUnavailableError
from triage.prompt import Source
from triage.retrieval import InMemoryRetriever, history_source

LABEL: dict[str, Any] = {"fault": "cable_loss", "cause": "frayed cable", "fix": "swapped it"}
ADDED_SIZE = len(DATASET_RUNS) + 1


def new_run(tmp_path: Path, name: str = "added") -> Case:
    """Write and load a resolved run that isn't in the data set."""
    return load_case(labelled_run(tmp_path / name, "cable_loss", "frayed cable", "swapped it"))


def loaded(tmp_path: Path, **fakes: Any) -> PersistentHistory:
    """Open the test history and load it."""
    history = open_history(tmp_path, **fakes)
    history.load()
    return history


def index_ids(history: PersistentHistory) -> set[str]:
    """Return the ids of the sources in the active index."""
    assert history.state.index is not None
    return {source.source_id for source in history.state.index.sources}


THREAD_TIMEOUT_S = 5.0
"""How long a test waits for a forced interleaving before failing rather than hanging."""


@contextmanager
def released(gate: threading.Event, *threads: threading.Thread) -> Iterator[None]:
    """Run a block, then always open the gate and join the threads, even if the block failed.

    A failed assertion before the gate opens would otherwise leave a thread blocked on it
    and hang the test run instead of reporting the failure.
    """
    try:
        yield
    finally:
        gate.set()
        for thread in threads:
            thread.join(timeout=THREAD_TIMEOUT_S)
    assert not any(thread.is_alive() for thread in threads)


def stored_runs(history: PersistentHistory) -> int:
    """Count the rows in the runs table."""
    assert history.database is not None
    return int(history.database.query("SELECT COUNT(*) FROM runs")[0][0])


def test_a_loaded_history_searches_exactly_as_an_in_memory_index(tmp_path: Path) -> None:
    """Replay from the cache gives the same neighbours, in the same order, as from_history."""
    history = loaded(tmp_path)
    plain = InMemoryRetriever.from_history(tmp_path / "dataset", FakeEmbedder())
    question = new_run(tmp_path)
    assert history.similar_to(question, len(DATASET_RUNS)) == plain.similar_to(
        question, len(DATASET_RUNS)
    )


def test_before_the_first_load_it_is_not_ready_and_never_searched(tmp_path: Path) -> None:
    """With no index yet, readiness says loading and a search refuses rather than answer empty."""
    history = open_history(tmp_path)
    reason = history.not_ready_reason()
    assert reason is not None and reason.startswith(LOADING_REASON)
    with pytest.raises(HistoryUnavailableError, match=LOADING_REASON):
        history.similar_to(new_run(tmp_path))


def test_a_run_added_over_http_survives_a_restart(tmp_path: Path) -> None:
    """A committed run is in the next process's index."""
    source = loaded(tmp_path).add(new_run(tmp_path), LABEL)
    restarted = loaded(tmp_path)
    assert len(restarted) == ADDED_SIZE
    assert source.source_id in {
        found.source_id for found in restarted.similar_to(new_run(tmp_path), ADDED_SIZE)
    }


def test_a_restart_with_ollama_down_loads_from_the_cache(tmp_path: Path) -> None:
    """A warm cache and the stored digest load the history without asking Ollama at all."""
    loaded(tmp_path).add(new_run(tmp_path), LABEL)
    offline = CountingEmbedder(down=True)
    history = loaded(tmp_path, embedder=offline, digest=FakeDigest(down=True))
    assert history.not_ready_reason() is None
    assert len(history) == ADDED_SIZE
    assert not offline.texts
    status = history.status()
    assert (status.digest, status.digest_verified) == (DIGEST, False)
    assert (status.cache_hits, status.cache_misses) == (ADDED_SIZE, 0)


@pytest.mark.parametrize(
    ("embedder", "digest"),
    [
        (CountingEmbedder(), FakeDigest(down=True)),
        (CountingEmbedder(down=True), FakeDigest()),
    ],
    ids=["no-stored-digest", "vectors-missing"],
)
def test_a_first_start_with_ollama_down_fails_the_load(
    tmp_path: Path, embedder: CountingEmbedder, digest: FakeDigest
) -> None:
    """Without a cache to fall back on, the load fails and the history stays not ready."""
    history = open_history(tmp_path, embedder=embedder, digest=digest)
    with pytest.raises(ModelUnavailableError):
        history.load()
    assert history.not_ready_reason() is not None


@pytest.mark.parametrize(
    ("second_label", "duplicate"),
    [
        (LABEL, True),
        (dict(reversed(list(LABEL.items()))), True),
        ({**LABEL, "fix": "replaced the connector"}, False),
    ],
    ids=["same", "same-keys-reordered", "different-fix"],
)
def test_a_duplicate_submission_changes_nothing(
    tmp_path: Path, second_label: dict[str, Any], duplicate: bool
) -> None:
    """The same run twice is one row and one index entry; a different fix is a new run."""
    embedder = CountingEmbedder()
    history = loaded(tmp_path, embedder=embedder)
    first = history.add(new_run(tmp_path), LABEL)
    asked = len(embedder.texts)
    second = history.add(new_run(tmp_path), second_label)
    assert (first.source_id == second.source_id) is duplicate
    expected = ADDED_SIZE if duplicate else ADDED_SIZE + 1
    assert (len(history), stored_runs(history)) == (expected, expected - len(DATASET_RUNS))
    if duplicate:
        assert len(embedder.texts) == asked
    assert history.not_ready_reason() is None


def test_a_failure_before_the_commit_leaves_nothing(tmp_path: Path) -> None:
    """When the run can't be embedded, no row is written and the index is untouched."""
    embedder = CountingEmbedder()
    history = loaded(tmp_path, embedder=embedder)
    embedder.down = True
    with pytest.raises(ModelUnavailableError):
        history.add(new_run(tmp_path), LABEL)
    assert (stored_runs(history), len(history)) == (0, len(DATASET_RUNS))
    assert history.not_ready_reason() is None


@pytest.mark.parametrize(
    "failure", [ValueError("shapes differ"), MemoryError(), ModelUnavailableError("gone")]
)
def test_a_failure_after_the_commit_marks_stale_then_a_rebuild_restores_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """The row is durable; the index stops serving until the rebuild, which includes the run."""
    history = loaded(tmp_path)

    def broken_add(*_: object) -> None:
        """Fail as an in-memory update can."""
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(InMemoryRetriever, "add", broken_add)
        source = history.add(new_run(tmp_path), LABEL)
    assert stored_runs(history) == 1
    assert history.not_ready_reason() == STALE_REASON
    assert history.rebuild.is_set()
    with pytest.raises(HistoryUnavailableError, match=STALE_REASON):
        history.similar_to(new_run(tmp_path))
    history.load()
    assert history.not_ready_reason() is None
    assert source.source_id in index_ids(history)


def test_a_stored_run_missing_from_memory_triggers_a_rebuild(tmp_path: Path) -> None:
    """A duplicate whose id the active index lacks means memory and disk diverged."""
    first = loaded(tmp_path)
    loaded(tmp_path).add(new_run(tmp_path), LABEL)
    first.add(new_run(tmp_path), LABEL)
    assert first.not_ready_reason() == STALE_REASON


@pytest.mark.parametrize("rebuild", [False, True], ids=["first-load", "rebuild"])
def test_a_run_added_during_a_load_is_never_lost_or_half_served(
    tmp_path: Path, rebuild: bool
) -> None:
    """A commit after the loader's snapshot is caught up before the swap; no partial index shows."""
    gate = threading.Event()
    embedder, digest = CountingEmbedder(gate=gate), FakeDigest()
    history = open_history(tmp_path, embedder=embedder, digest=digest)
    if rebuild:
        history.load()
        digest.digest = NEW_DIGEST
    loader = threading.Thread(target=history.load, name="loader")
    loader.start()
    with released(gate, loader):
        assert embedder.entered.wait(timeout=THREAD_TIMEOUT_S)
        source = history.add(new_run(tmp_path), LABEL)
        if rebuild:
            assert len(history) == ADDED_SIZE
        else:
            with pytest.raises(HistoryUnavailableError):
                history.similar_to(new_run(tmp_path))
    assert history.not_ready_reason() is None
    assert len(history) == ADDED_SIZE
    assert source.source_id in index_ids(history)


@pytest.mark.parametrize("embedded_before_failure", [0, 1, 2])
def test_an_interrupted_load_resumes_from_the_cache(
    tmp_path: Path, embedded_before_failure: int
) -> None:
    """The vectors a failed load embedded are reused; the next load asks only for the rest."""
    with pytest.raises(ModelUnavailableError):
        loaded(tmp_path, embedder=CountingEmbedder(fail_after=embedded_before_failure))
    retry = CountingEmbedder()
    loaded(tmp_path, embedder=retry)
    assert len(retry.texts) == len(DATASET_RUNS) - embedded_before_failure


def test_a_corrupt_database_is_not_ready_with_the_fix_and_kept(tmp_path: Path) -> None:
    """An unusable file blocks every use with one line saying to move it aside."""
    path = tmp_path / "history.db"
    path.write_bytes(b"not a database")
    history = open_history(tmp_path)
    reason = history.not_ready_reason()
    assert reason is not None and "move it aside" in reason
    for use in (history.load, lambda: history.add(new_run(tmp_path), LABEL)):
        with pytest.raises(HistoryUnavailableError, match="move it aside"):
            use()
    assert path.read_bytes() == b"not a database"


def test_a_changed_digest_makes_it_not_ready_until_rebuilt(tmp_path: Path) -> None:
    """A new model under the same name is detected, rebuilt under its digest, and pruned."""
    digest = FakeDigest()
    history = loaded(tmp_path, digest=digest)
    digest.digest = NEW_DIGEST
    assert history.verify_digest() is True
    assert history.not_ready_reason() == MODEL_CHANGED_REASON
    history.load()
    assert history.not_ready_reason() is None
    assert history.status().digest == NEW_DIGEST
    assert history.database is not None
    digests = history.database.query("SELECT DISTINCT digest FROM vectors")
    assert digests == [(NEW_DIGEST,)]


@pytest.mark.parametrize("ollama_down", [False, True], ids=["same-digest", "ollama-down"])
def test_an_unchanged_or_unanswered_digest_check_keeps_it_ready(
    tmp_path: Path, ollama_down: bool
) -> None:
    """Only a changed digest triggers a rebuild; an outage leaves the index serving."""
    digest = FakeDigest()
    history = loaded(tmp_path, digest=digest)
    digest.down = ollama_down
    assert history.verify_digest() is False
    assert history.not_ready_reason() is None
    assert history.status().digest_verified is True


def test_a_history_source_is_stored_word_for_word(tmp_path: Path) -> None:
    """The replayed source is the one the run became, cause and fix included."""
    run = new_run(tmp_path)
    expected = history_source(run, LABEL)
    loaded(tmp_path).add(run, LABEL)
    index = loaded(tmp_path).state.index
    assert index is not None
    replayed = {source.source_id: source for source in index.sources}
    assert replayed[expected.source_id] == expected


DISK_FULL = DatabaseError("the history database history.db failed (database or disk is full)")


@pytest.mark.parametrize("step", ["embed", "insert"])
def test_a_database_failure_while_adding_is_unavailable_and_adds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """A full disk at either write becomes HistoryUnavailableError; memory is untouched."""
    history = loaded(tmp_path)

    def full(*_: object, **__: object) -> None:
        """Fail as a full disk does."""
        raise DISK_FULL

    if step == "embed":
        monkeypatch.setattr(EmbeddingCache, "store", full)
    else:
        monkeypatch.setattr(history_store, "insert_run", full)
    with pytest.raises(HistoryUnavailableError, match="disk is full"):
        history.add(new_run(tmp_path), LABEL)
    assert len(history) == len(DATASET_RUNS)
    assert history.not_ready_reason() is None


def test_a_database_failure_after_the_commit_marks_the_index_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run is committed, so a failed cache read in the in-memory add means a rebuild."""
    history = loaded(tmp_path)
    embedder = history.state.embedder
    assert embedder is not None
    calls: list[int] = []
    embed_and_store = embedder.embed_and_store

    def fails_second_time(texts: Any) -> list[list[float]]:
        """Store for the writer, then fail for the in-memory add."""
        calls.append(1)
        if len(calls) > 1:
            raise DISK_FULL
        return embed_and_store(texts)

    monkeypatch.setattr(embedder, "embed_and_store", fails_second_time)
    source = history.add(new_run(tmp_path), LABEL)
    assert history.not_ready_reason() == STALE_REASON
    assert history.rebuild.is_set()
    monkeypatch.undo()
    history.load()
    assert source.source_id in index_ids(history)


def test_a_database_failure_while_searching_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache read that fails while embedding the question is a 503-shaped error, not a crash."""
    history = loaded(tmp_path)

    def broken(*_: object, **__: object) -> dict[str, list[float]]:
        """Fail as an I/O error does."""
        raise DISK_FULL

    monkeypatch.setattr(EmbeddingCache, "vectors", broken)
    with pytest.raises(HistoryUnavailableError, match="disk is full"):
        history.similar_to(new_run(tmp_path))


def test_a_status_query_that_fails_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dependency report gets the reason and no count, rather than an exception."""
    history = loaded(tmp_path)

    def broken(*_: object, **__: object) -> list[tuple[Any, ...]]:
        """Fail as an I/O error does."""
        raise DISK_FULL

    assert history.database is not None
    monkeypatch.setattr(history.database, "query", broken)
    found = history.status()
    assert found.stored_runs is None
    assert found.error is not None and "disk is full" in found.error
    assert not found.healthy


@pytest.mark.parametrize(
    "label_text",
    ["{}", '{"fault": "cable_loss"}', '{"cause": "c"}', "[]", "not json", '"text"'],
    ids=["empty", "no-cause", "no-fix", "list", "not-json", "string"],
)
def test_an_unusable_dataset_label_names_its_folder(tmp_path: Path, label_text: str) -> None:
    """A history run whose label can't make a source stops the load with a CaseError naming it."""
    history = open_history(tmp_path)
    folder = tmp_path / "dataset" / DATASET_RUNS[0][0]
    (folder / "label.json").write_text(label_text, encoding="utf-8")
    with pytest.raises(CaseError, match=DATASET_RUNS[0][0]):
        history.load()


class BlockingList(list[Source]):
    """A source list whose append waits on a gate, to hold a writer mid-add."""

    def __init__(self, items: list[Source], gate: threading.Event) -> None:
        """Copy the items; append waits on the gate after signalling it got there."""
        super().__init__(items)
        self.gate = gate
        self.entered = threading.Event()

    def append(self, item: Source) -> None:
        """Signal, wait for the gate, then append."""
        self.entered.set()
        self.gate.wait(timeout=THREAD_TIMEOUT_S)
        super().append(item)


def test_a_search_never_sees_a_vector_without_its_source(tmp_path: Path) -> None:
    """A writer holding the index between its new vector and its new source can't break a search.

    The question is held while embedding it; the writer then stops inside the index's add,
    after the vectors grew but before the source list did. The search must finish on the
    sources and vectors it started with, not on the half-updated index.
    """
    question_gate, append_gate = threading.Event(), threading.Event()
    embedder = CountingEmbedder(gate=question_gate, blocked_thread="reader")
    history = loaded(tmp_path, embedder=embedder)
    index = history.state.index
    assert index is not None
    blocking = BlockingList(index.sources, append_gate)
    index.sources = blocking
    found: list[list[Source]] = []
    reader = threading.Thread(
        target=lambda: found.append(history.similar_to(new_run(tmp_path, "question"))),
        name="reader",
    )
    writer = threading.Thread(target=lambda: history.add(new_run(tmp_path), LABEL), name="writer")
    reader.start()
    with released(append_gate, writer), released(question_gate, reader):
        assert embedder.entered.wait(timeout=THREAD_TIMEOUT_S)
        writer.start()
        assert blocking.entered.wait(timeout=THREAD_TIMEOUT_S)
        question_gate.set()
        reader.join(timeout=THREAD_TIMEOUT_S)
    assert len(found) == 1
    assert {source.fault for source in found[0]} <= {run[1] for run in DATASET_RUNS}


def test_a_run_committed_during_a_digest_swap_is_cached_under_the_new_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run embedded under the old digest is stored again under the new one before it is indexed.

    The writer is held after embedding and before committing; meanwhile a load swaps in a new
    digest and prunes the old vectors. An offline restart must still load the run from the cache.
    """
    digest = FakeDigest()
    history = loaded(tmp_path, digest=digest)
    gate, entered = threading.Event(), threading.Event()
    insert = history_store.insert_run

    def held(*arguments: Any) -> bool:
        """Wait for the swap, then commit."""
        entered.set()
        gate.wait(timeout=THREAD_TIMEOUT_S)
        return insert(*arguments)

    monkeypatch.setattr(history_store, "insert_run", held)
    run = new_run(tmp_path)
    writer = threading.Thread(target=lambda: history.add(run, LABEL), name="writer")
    writer.start()
    with released(gate, writer):
        assert entered.wait(timeout=THREAD_TIMEOUT_S)
        digest.digest = NEW_DIGEST
        history.load()
    assert history.not_ready_reason() is None
    added = history_source(run, LABEL).source_id
    assert added in index_ids(history)
    offline = loaded(tmp_path, embedder=CountingEmbedder(down=True), digest=FakeDigest(down=True))
    assert added in index_ids(offline)
