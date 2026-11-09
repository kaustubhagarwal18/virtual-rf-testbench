"""Tests for the triage service over HTTP, with a fake model and a fake embedder."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, get_args

import numpy as np
import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from triage.api import MAX_LINE_LENGTH, MAX_LINES, EvidenceLevel, create_app, main
from triage.embed import FakeEmbedder
from triage.evidence import LEVELS, SavedRun, build_evidence
from triage.llm import FakeProvider, ModelUnavailableError
from triage.prompt import build_prompt
from triage.retrieval import InMemoryRetriever

CABLE_REPLY = json.dumps({"fix": "CABLE", "where": "the cable", "because": "loss", "cited": []})
FIRMWARE_REPLY = json.dumps({"fix": "FIRMWARE", "where": "fw", "because": "x", "cited": []})
RESULTS: dict[str, Any] = {"tests": [FAILING_TEST]}
LABEL = {"fault": "cable_loss", "cause": "worn cable", "fix": "replaced it"}


def empty_history() -> InMemoryRetriever:
    """Return a history with nothing in it, embedding offline."""
    return InMemoryRetriever([], np.zeros((0, 0)), FakeEmbedder())


class Unreachable:
    """A model that is never reachable."""

    def complete(self, system: str, user: str) -> str:
        """Fail as an unreachable model does."""
        raise ModelUnavailableError("can't reach Ollama; start it with 'ollama serve'")


def test_health_says_how_much_history_is_loaded() -> None:
    """The health check answers, and counts the history."""
    client = TestClient(create_app(FakeProvider([CABLE_REPLY]), empty_history()))
    assert client.get("/health").json() == {"status": "ok", "retrieval": True, "history_size": 0}
    bare = TestClient(create_app(FakeProvider([CABLE_REPLY])))
    assert bare.get("/health").json()["retrieval"] is False


def test_a_posted_run_comes_back_as_a_verdict() -> None:
    """The run's evidence reaches the model, and its answer comes back as the verdict."""
    provider = FakeProvider([CABLE_REPLY])
    client = TestClient(create_app(provider))
    response = client.post("/triage", json={"results": RESULTS, "logs": ["a line"]})
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert (body["fix"], body["where"], body["cited"]) == ("CABLE", "the cable", [])
    assert "level_1000mhz" in provider.prompts[0][1]


def test_an_added_run_is_offered_as_a_source_next_time() -> None:
    """A resolved run posted to /history is retrieved, under a neutral id, for a later question."""
    provider = FakeProvider([CABLE_REPLY])
    client = TestClient(create_app(provider, empty_history()))
    added = client.post("/history", json={"results": RESULTS, "label": LABEL})
    assert added.status_code == status.HTTP_201_CREATED
    source_id = added.json()["source_id"]
    assert source_id.startswith("past-")
    assert added.json()["history_size"] == 1
    client.post("/triage", json={"results": RESULTS})
    assert f"[{source_id}]" in provider.prompts[0][1]
    assert "worn cable" in provider.prompts[0][1]


def test_retrieval_can_be_switched_off_per_request() -> None:
    """With retrieve false, no past run is offered even when the history has some."""
    history = empty_history()
    provider = FakeProvider([CABLE_REPLY])
    client = TestClient(create_app(provider, history))
    client.post("/history", json={"results": RESULTS, "label": LABEL})
    client.post("/triage", json={"results": RESULTS, "retrieve": False})
    assert "SOURCES\nNone" in provider.prompts[0][1]


def test_the_firmware_check_applies_to_the_service() -> None:
    """A FIRMWARE answer on unchanged firmware comes back UNKNOWN, with the reason."""
    results = {**RESULTS, "firmware_versions": {"analyzer": "0.1.0", "generator": "0.1.0"}}
    client = TestClient(create_app(FakeProvider([FIRMWARE_REPLY])))
    body = client.post("/triage", json={"results": results}).json()
    assert body["fix"] == "UNKNOWN"
    assert "unchanged" in body["overruled"]


@pytest.mark.parametrize(
    ("provider", "code", "detail"),
    [
        (Unreachable(), status.HTTP_503_SERVICE_UNAVAILABLE, "ollama serve"),
        (FakeProvider(["not json"]), status.HTTP_502_BAD_GATEWAY, "couldn't be used"),
    ],
)
def test_a_model_problem_is_one_line_with_a_status(provider: Any, code: int, detail: str) -> None:
    """An unreachable model is 503 and an unusable answer 502, each with a one-line detail."""
    response = TestClient(create_app(provider)).post("/triage", json={"results": RESULTS})
    assert response.status_code == code
    assert detail in response.json()["detail"]


def test_adding_history_without_one_is_a_conflict() -> None:
    """A service started with --no-history refuses /history and says how to fix it."""
    client = TestClient(create_app(FakeProvider([CABLE_REPLY])))
    response = client.post("/history", json={"results": RESULTS, "label": LABEL})
    assert response.status_code == status.HTTP_409_CONFLICT
    assert "--no-history" in response.json()["detail"]


MEASURED_TEST: dict[str, Any] = {
    **FAILING_TEST,
    "measurements": [{"name": "level", "value": -13.0, "unit": "dBm", "passed": False}],
}
"""A test with one well-formed measurement, for corrupting one field at a time."""


def with_measurement(**changes: object) -> dict[str, Any]:
    """Return a results file whose one measurement has the given fields changed."""
    measurement = {**MEASURED_TEST["measurements"][0], **changes}
    return {"tests": [{**MEASURED_TEST, "measurements": [measurement]}]}


SELECTION = {"collected": 20, "selected": 20, "deselected": 0}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"results": {}},
        {"results": {"tests": [{"id": "t"}]}},
        {"results": {"tests": "all of them"}},
        {"results": RESULTS, "level": "everything"},
        {"results": RESULTS, "logs": ["x"] * (MAX_LINES + 1)},
        {"results": RESULTS, "sequence": ["x"] * (MAX_LINES + 1)},
        {"results": RESULTS, "logs": ["x" * (MAX_LINE_LENGTH + 1)]},
        {"results": RESULTS, "sequence": [{"step": 1}]},
        {"results": {**RESULTS, "firmware_versions": "0.1.0"}},
        {"results": {**RESULTS, "firmware_versions": ["0.1.0"]}},
        {"results": {**RESULTS, "firmware_versions": {"generator": {"v": 1}}}},
        {"results": {**RESULTS, "selection": "all"}},
        {"results": {**RESULTS, "selection": {"collected": 20}}},
        {"results": {**RESULTS, "selection": {**SELECTION, "selected": -1}}},
        {"results": {**RESULTS, "selection": SELECTION, "totals": [1]}},
        {"results": {**RESULTS, "selection": SELECTION, "totals": {"passed": "many"}}},
        {"results": {"tests": [{**FAILING_TEST, "measurements": "x"}]}},
        {"results": {"tests": [{**FAILING_TEST, "measurements": ["x"]}]}},
        {"results": {"tests": [{**FAILING_TEST, "measurements": [{"name": "level"}]}]}},
        {"results": with_measurement(passed="sometimes")},
        {"results": with_measurement(value=[1.0])},
        {"results": with_measurement(limit_low={"dB": 1})},
    ],
)
def test_a_malformed_run_is_refused_before_the_model_is_asked(body: dict[str, Any]) -> None:
    """Missing, malformed or oversized evidence is a 422, and the model is never asked.

    Every field the evidence builder reads is covered, with a wrong type and a wrong shape,
    so none of them can reach the pipeline and fail there as a 500.
    """
    provider = FakeProvider([CABLE_REPLY])
    response = TestClient(create_app(provider)).post("/triage", json=body)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert not provider.prompts


@pytest.mark.parametrize("missing", ["fault", "cause", "fix"])
def test_a_label_missing_a_field_is_refused(missing: str) -> None:
    """A resolved run must say what was wrong, why and what fixed it."""
    label = {key: value for key, value in LABEL.items() if key != missing}
    client = TestClient(create_app(FakeProvider([CABLE_REPLY]), empty_history()))
    response = client.post("/history", json={"results": RESULTS, "label": label})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_main_serves_on_localhost_by_default() -> None:
    """Without --host the service listens on this machine only."""
    calls: list[tuple[str, int]] = []

    def serve(app: object, host: str, port: int) -> None:
        """Record where the app would be served."""
        del app
        calls.append((host, port))

    assert main(["--no-history"], serve) == 0
    assert calls == [("127.0.0.1", 8000)]


def test_main_says_how_to_build_a_missing_history(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A history folder that doesn't exist exits 1 with the command that builds it."""
    assert main(["--history", str(tmp_path / "absent")], lambda *_, **__: None) == 1
    assert "make fault-dataset" in capsys.readouterr().out


def test_main_reports_an_unreachable_embedder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the history can't be embedded, the service exits 1 with one line."""
    write_run(tmp_path / "history" / "cable_loss-1", [FAILING_TEST])

    def unreachable(*_: object) -> InMemoryRetriever:
        """Fail as an unreachable embedder does."""
        raise ModelUnavailableError("start it with 'ollama serve'")

    monkeypatch.setattr(InMemoryRetriever, "from_history", unreachable)
    assert main(["--history", str(tmp_path / "history")], lambda *_, **__: None) == 1
    assert "ollama serve" in capsys.readouterr().out


def test_the_api_runs_as_a_module() -> None:
    """Python -m triage.api works the way make triage-api calls it."""
    completed = subprocess.run(
        [sys.executable, "-m", "triage.api", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Serve triage over HTTP" in completed.stdout


def test_the_api_offers_exactly_the_pipelines_evidence_levels() -> None:
    """The request's level type and the pipeline's levels can't drift apart."""
    assert set(get_args(EvidenceLevel)) == set(LEVELS)


def test_adding_history_with_an_unreachable_embedder_is_unavailable() -> None:
    """When the run can't be embedded, /history is 503 with the hint, and nothing is added."""

    class DownEmbedder:
        """An embedder that is never reachable."""

        def embed(self, texts: object) -> list[list[float]]:
            """Fail as an unreachable embedder does."""
            raise ModelUnavailableError("start it with 'ollama serve'")

    history = InMemoryRetriever([], np.zeros((0, 0)), DownEmbedder())
    client = TestClient(create_app(FakeProvider([CABLE_REPLY]), history))
    response = client.post("/history", json={"results": RESULTS, "label": LABEL})
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "ollama serve" in response.json()["detail"]
    assert len(history) == 0


@pytest.mark.parametrize("value", [-13.0, 0, "NaN", "Infinity", "-Infinity", None], ids=repr)
def test_every_reading_a_record_can_hold_is_accepted(value: object) -> None:
    """Finite and non-finite readings, and a missing bound, are all valid evidence."""
    client = TestClient(create_app(FakeProvider([CABLE_REPLY])))
    results = with_measurement(value=value, limit_low=value, margin=value)
    assert client.post("/triage", json={"results": results}).status_code == status.HTTP_200_OK


SAVED_RUNS = sorted(path for path in Path("triage/data/runs").glob("*/*") if path.is_dir())
"""Every saved run in the data set: real results files, logs and sequence logs."""


@pytest.mark.parametrize(
    "folder", SAVED_RUNS, ids=lambda folder: f"{folder.parent.name}/{folder.name}"
)
def test_a_real_run_posted_gives_the_model_what_the_saved_run_does(folder: Path) -> None:
    """Posting a saved run's files builds exactly the prompt reading them from disk builds."""
    run = SavedRun.load(folder)
    provider = FakeProvider([CABLE_REPLY])
    body = {"results": run.results, "logs": run.logs, "sequence": run.sequence, "retrieve": False}
    response = TestClient(create_app(provider)).post("/triage", json=body)
    assert response.status_code == status.HTTP_200_OK
    assert provider.prompts == [build_prompt(build_evidence(run))]


def test_the_data_set_has_every_saved_run() -> None:
    """The real-run test above covers all 54 runs, not an empty list."""
    expected_runs = 54
    assert len(SAVED_RUNS) == expected_runs
