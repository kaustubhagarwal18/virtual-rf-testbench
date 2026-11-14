"""Tests for the triage API with a persistent history: readiness, outages and wiring."""

from pathlib import Path
from typing import Any

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tests.unit.triage.history_fakes import (
    DATASET_RUNS,
    DIGEST,
    DOWN,
    CountingEmbedder,
    FakeDigest,
    open_history,
    write_dataset,
)
from tests.unit.triage.test_api import CABLE_REPLY, LABEL, RESULTS
from triage import history_store
from triage.api import create_app, main
from triage.embed_cache import EmbeddingCache
from triage.health import ServiceHealth
from triage.history_db import DatabaseError
from triage.history_loader import HistoryLoader
from triage.history_store import STALE_REASON, PersistentHistory
from triage.llm import FakeProvider

REPLIES = 10
"""Enough canned model replies for any one test."""


def client_for(history: PersistentHistory) -> TestClient:
    """Serve a persistent history the way main wires it, with a fake model."""
    health = ServiceHealth(readiness=history, store=history.status)
    return TestClient(
        create_app(FakeProvider([CABLE_REPLY] * REPLIES), history, service_health=health)
    )


def test_ollama_down_at_start_up_keeps_the_process_up_and_not_ready(tmp_path: Path) -> None:
    """Live but not ready; requests that need the history get 503 until Ollama returns."""
    digest = FakeDigest(down=True)
    history = open_history(tmp_path, digest=digest)
    loader = HistoryLoader(history)
    assert loader.attempt() is False
    client = client_for(history)
    assert client.get("/livez").status_code == status.HTTP_200_OK
    ready = client.get("/readyz")
    assert ready.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert DOWN in ready.json()["reason"]
    for path, body in (
        ("/triage", {"results": RESULTS}),
        ("/history", {"results": RESULTS, "label": LABEL}),
    ):
        assert client.post(path, json=body).status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    digest.down = False
    assert loader.attempt() is True
    assert client.get("/readyz").status_code == status.HTTP_200_OK
    assert client.post("/triage", json={"results": RESULTS}).status_code == status.HTTP_200_OK


def test_ollama_down_after_ready_answers_503_without_going_unready(tmp_path: Path) -> None:
    """The index stays loaded, so readiness holds; only the request that needs Ollama fails."""
    embedder = CountingEmbedder()
    history = open_history(tmp_path, embedder=embedder)
    history.load()
    client = client_for(history)
    embedder.down = True
    response = client.post("/triage", json={"results": RESULTS})
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "ollama serve" in response.json()["detail"]
    assert client.get("/readyz").status_code == status.HTTP_200_OK
    assert client.get("/livez").status_code == status.HTTP_200_OK


def test_a_stale_index_is_not_ready_and_never_searched(tmp_path: Path) -> None:
    """After a failed update, readiness and triage both say the index is being rebuilt."""
    history = open_history(tmp_path)
    history.load()
    with history.lock:
        history.state.rebuild_reason = STALE_REASON
    client = client_for(history)
    assert client.get("/readyz").json() == {"status": "not ready", "reason": STALE_REASON}
    response = client.post("/triage", json={"results": RESULTS})
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["detail"] == STALE_REASON


def test_an_added_run_is_kept_across_a_restart_and_a_duplicate_is_idempotent(
    tmp_path: Path,
) -> None:
    """POST /history persists; a repeat returns the same body; a restart still has the run."""
    history = open_history(tmp_path)
    history.load()
    client = client_for(history)
    body = {"results": RESULTS, "label": LABEL}
    first = client.post("/history", json=body)
    again = client.post("/history", json=body)
    assert first.status_code == again.status_code == status.HTTP_201_CREATED
    assert (
        first.json()
        == again.json()
        == {
            "source_id": first.json()["source_id"],
            "history_size": len(DATASET_RUNS) + 1,
        }
    )
    restarted = open_history(tmp_path)
    restarted.load()
    assert client_for(restarted).get("/health").json()["history_size"] == len(DATASET_RUNS) + 1


@pytest.mark.parametrize(("ollama_down", "expected"), [(False, "ok"), (True, "degraded")])
def test_the_dependency_report_shows_the_store(
    tmp_path: Path, ollama_down: bool, expected: str
) -> None:
    """The digest in use, whether it is verified, and the cache counts; always 200."""
    open_history(tmp_path).load()
    history = open_history(tmp_path, digest=FakeDigest(down=ollama_down))
    history.load()
    response = client_for(history).get("/health/dependencies")
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["status"] == expected
    assert body["store"] == {
        "digest": DIGEST,
        "digest_verified": not ollama_down,
        "stored_runs": 0,
        "cache_hits": len(DATASET_RUNS),
        "cache_misses": 0,
        "error": None,
    }


class Started:
    """Records the loaders main starts, without running them."""

    def __init__(self) -> None:
        """Start with none recorded."""
        self.loaders: list[HistoryLoader] = []
        self.apps: list[Any] = []

    def start(self, loader: HistoryLoader) -> None:
        """Record a loader."""
        self.loaders.append(loader)

    def serve(self, app: object, host: str, port: int) -> None:
        """Record the app instead of serving it."""
        del host, port
        self.apps.append(app)


@pytest.mark.parametrize("from_flag", [False, True], ids=["env", "flag"])
def test_main_wires_the_database_loader_and_readiness(tmp_path: Path, from_flag: bool) -> None:
    """--history-db or TRIAGE_HISTORY_DB opens the file, starts a loader and gates readiness."""
    dataset = write_dataset(tmp_path / "dataset")
    path = tmp_path / "history.db"
    arguments = ["--history", str(dataset)]
    environ = (
        {"TRIAGE_HISTORY_DB": str(tmp_path / "ignored.db")}
        if from_flag
        else {"TRIAGE_HISTORY_DB": str(path)}
    )
    if from_flag:
        arguments += ["--history-db", str(path)]
    started = Started()
    assert main(arguments, started.serve, environ, started.start) == 0
    assert len(started.loaders) == 1
    database = started.loaders[0].history.database
    assert database is not None and database.path == path
    ready = TestClient(started.apps[0]).get("/readyz")
    assert ready.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--no-history", "--history-db", "{tmp}/h.db"], "--no-history"),
        (["--history", "{tmp}/absent", "--history-db", "{tmp}/h.db"], "make fault-dataset"),
        (["--history", "{tmp}/dataset", "--history-db", "{tmp}/missing/h.db"], "doesn't exist"),
    ],
    ids=["no-history", "no-dataset", "no-folder"],
)
def test_main_refuses_an_unusable_history_db_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arguments: list[str], message: str
) -> None:
    """A setting that can't work stops start-up with one line, and no loader starts."""
    write_dataset(tmp_path / "dataset")
    started = Started()
    filled = [argument.format(tmp=tmp_path) for argument in arguments]
    assert main(filled, started.serve, {}, started.start) == 1
    output = capsys.readouterr().out.strip().splitlines()
    assert len(output) == 1 and message in output[0]
    assert (started.loaders, started.apps) == ([], [])


def test_main_without_a_history_db_starts_no_loader(tmp_path: Path) -> None:
    """Unset, the service is the in-memory one it always was: no database, always ready."""
    started = Started()
    assert main(["--no-history"], started.serve, {}, started.start) == 0
    assert not started.loaders
    assert TestClient(started.apps[0]).get("/readyz").status_code == status.HTTP_200_OK
    assert not (tmp_path / "history.db").exists()


DISK_FULL = DatabaseError("the history database history.db failed (database or disk is full)")


def full_disk(*_: object, **__: object) -> Any:
    """Fail as a full disk does."""
    raise DISK_FULL


@pytest.mark.parametrize("step", ["embed", "insert"])
def test_a_database_failure_while_adding_history_is_a_one_line_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """POST /history on a full disk is 503 with the reason, never a 500."""
    history = open_history(tmp_path)
    history.load()
    if step == "embed":
        monkeypatch.setattr(EmbeddingCache, "store", full_disk)
    else:
        monkeypatch.setattr(history_store, "insert_run", full_disk)
    response = client_for(history).post("/history", json={"results": RESULTS, "label": LABEL})
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "disk is full" in response.json()["detail"]
    assert "\n" not in response.json()["detail"]


def test_a_database_failure_while_triaging_is_a_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache read that fails while embedding the question is 503, never a 500."""
    history = open_history(tmp_path)
    history.load()
    monkeypatch.setattr(EmbeddingCache, "vectors", full_disk)
    response = client_for(history).post("/triage", json={"results": RESULTS})
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "disk is full" in response.json()["detail"]


def test_a_store_that_cant_be_read_leaves_the_report_200_and_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dependency report still answers, shows the store's error, and says degraded."""
    history = open_history(tmp_path)
    history.load()
    assert history.database is not None
    monkeypatch.setattr(history.database, "query", full_disk)
    response = client_for(history).get("/health/dependencies")
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["status"] == "degraded"
    assert body["store"]["stored_runs"] is None
    assert "disk is full" in body["store"]["error"]
    assert client_for(history).get("/livez").status_code == status.HTTP_200_OK
