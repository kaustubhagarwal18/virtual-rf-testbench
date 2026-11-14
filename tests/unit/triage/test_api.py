"""Tests for the triage service over HTTP, with a fake model and a fake embedder."""

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

import numpy as np
import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from tools.faults import CATALOGUE
from triage.abstain import SERVICE_GATES, PrecedentsDisagree
from triage.api import (
    MAX_LINE_LENGTH,
    MAX_LINES,
    ActionKey,
    EvidenceLevel,
    create_app,
    main,
)
from triage.bench_run import load_case
from triage.embed import DEFAULT_EMBED_MODEL, FakeEmbedder, OllamaEmbedder
from triage.evidence import LEVELS, build_evidence
from triage.health import ModelStatus, OllamaStatus, ServiceHealth
from triage.llm import DEFAULT_HOST, DEFAULT_MODEL, FakeProvider, ModelUnavailableError
from triage.pack import DEFAULT_PACK
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

    def complete(self, system: str, user: str, schema: object = None) -> str:
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


def read_lines(path: Path) -> list[str]:
    """Return a saved run file's lines, or none when the run doesn't have it."""
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


@pytest.mark.parametrize(
    "folder", SAVED_RUNS, ids=lambda folder: f"{folder.parent.name}/{folder.name}"
)
def test_a_real_run_posted_gives_the_model_what_the_saved_run_does(folder: Path) -> None:
    """Posting a saved run's files builds exactly the prompt reading them from disk builds."""
    run = load_case(folder)
    provider = FakeProvider([CABLE_REPLY])
    body = {
        "results": json.loads((folder / "results.json").read_text(encoding="utf-8")),
        "logs": read_lines(folder / "logs.txt"),
        "sequence": read_lines(folder / "sequence.log"),
        "retrieve": False,
    }
    response = TestClient(create_app(provider)).post("/triage", json=body)
    assert response.status_code == status.HTTP_200_OK
    assert provider.prompts == [build_prompt(build_evidence(run))]


def test_the_data_set_has_every_saved_run() -> None:
    """The real-run test above covers six runs for every catalogue fault, not an empty list."""
    runs_per_fault = 6
    assert len(SAVED_RUNS) == runs_per_fault * len(CATALOGUE)


def test_the_api_accepts_exactly_the_action_keys_on_the_list() -> None:
    """A label's action type and the list of actions can't drift apart."""
    assert set(get_args(ActionKey)) == set(DEFAULT_PACK.keys)


def test_an_added_runs_action_reaches_a_later_prompt() -> None:
    """A resolved run posted with its action shows the model that action as a precedent."""
    provider = FakeProvider([CABLE_REPLY])
    client = TestClient(create_app(provider, empty_history()))
    added = client.post(
        "/history", json={"results": RESULTS, "label": {**LABEL, "action": "CABLE"}}
    )
    assert added.status_code == status.HTTP_201_CREATED
    client.post("/triage", json={"results": RESULTS})
    assert "Action: CABLE" in provider.prompts[0][1]


@pytest.mark.parametrize("action", ["cable", "REPLACE", "", 3])
def test_an_action_off_the_list_is_refused(action: object) -> None:
    """Only the list's keys, spelt exactly, are accepted as a label's action."""
    client = TestClient(create_app(FakeProvider([CABLE_REPLY]), empty_history()))
    response = client.post(
        "/history", json={"results": RESULTS, "label": {**LABEL, "action": action}}
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_the_service_abstains_when_its_precedents_disagree() -> None:
    """Two past runs resolved two ways: UNKNOWN with the reason, and the model isn't asked."""
    provider = FakeProvider([CABLE_REPLY])
    client = TestClient(create_app(provider, empty_history()))
    for action, cause in [("CABLE", "worn cable"), ("LINK", "unplugged cable")]:
        label = {**LABEL, "cause": cause, "action": action}
        assert (
            client.post("/history", json={"results": RESULTS, "label": label}).status_code
            == status.HTTP_201_CREATED
        )
    body = client.post("/triage", json={"results": RESULTS}).json()
    assert body["fix"] == "UNKNOWN"
    assert "CABLE" in body["gate_reason"] and "LINK" in body["gate_reason"]
    assert body["cited"] == []
    assert not provider.prompts


def test_the_service_asks_the_model_when_its_precedents_agree() -> None:
    """Agreeing past runs pass the gate, and the reply says no gate answered."""
    provider = FakeProvider([CABLE_REPLY])
    client = TestClient(create_app(provider, empty_history()))
    client.post("/history", json={"results": RESULTS, "label": {**LABEL, "action": "CABLE"}})
    body = client.post("/triage", json={"results": RESULTS}).json()
    assert (body["fix"], body["gate_reason"]) == ("CABLE", "")
    assert len(provider.prompts) == 1


def test_the_service_applies_the_disagreement_gate_only() -> None:
    """The similarity gate cost more than it saved (ADR 0013), so the API leaves it out."""
    assert [type(gate) for gate in SERVICE_GATES] == [PrecedentsDisagree]


def test_the_verdict_states_its_category_and_what_fixed_a_cited_run() -> None:
    """The response says bench, firmware or test, and quotes each cited run's recorded fix."""
    provider = FakeProvider([CABLE_REPLY])
    client = TestClient(create_app(provider, empty_history()))
    source_id = client.post("/history", json={"results": RESULTS, "label": LABEL}).json()[
        "source_id"
    ]
    provider.replies = [
        json.dumps({"fix": "CABLE", "where": "cable", "because": "x", "cited": [source_id]})
    ]
    body = client.post("/triage", json={"results": RESULTS}).json()
    assert body["category"] == "bench"
    assert body["past_fixes"] == [{"source_id": source_id, "fix_applied": "replaced it"}]


def test_main_reports_a_malformed_history_case_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A broken case.json in the history stops start-up with one line naming it."""
    broken = tmp_path / "history" / "batch-1"
    broken.mkdir(parents=True)
    (broken / "case.json").write_text("{", encoding="utf-8")
    assert main(["--history", str(tmp_path / "history")], lambda *_, **__: None) == 1
    assert "case.json" in capsys.readouterr().out


OLLAMA = "http://ollama.test:11434"


class NotReady:
    """A readiness that is still waiting for its history."""

    def not_ready_reason(self) -> str | None:
        """Say what it is waiting for."""
        return "history still loading"


def ollama_up() -> OllamaStatus:
    """Report Ollama up with the answering model pulled."""
    return OllamaStatus(OLLAMA, True, "0.40.14", None, (ModelStatus("qwen2.5:3b", True, "d1"),))


def ollama_down() -> OllamaStatus:
    """Report Ollama unreachable."""
    return OllamaStatus(
        OLLAMA, False, None, "connection refused", (ModelStatus("qwen2.5:3b", False, None),)
    )


def probed(health: ServiceHealth) -> TestClient:
    """Return a client for a service with the given readiness and dependency check."""
    return TestClient(create_app(FakeProvider([CABLE_REPLY]), service_health=health))


def test_livez_answers_even_when_not_ready_and_ollama_is_down() -> None:
    """Liveness depends on nothing, so neither a loading history nor an outage fails it."""
    response = probed(ServiceHealth(NotReady(), ollama_down)).get("/livez")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


def test_readyz_is_ready_by_default() -> None:
    """A service whose history was loaded before serving is ready."""
    response = probed(ServiceHealth()).get("/readyz")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ready", "reason": ""}


def test_readyz_is_503_with_the_reason_while_not_ready() -> None:
    """Not ready is a 503 that says what it is waiting for."""
    response = probed(ServiceHealth(NotReady())).get("/readyz")
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {"status": "not ready", "reason": "history still loading"}


def test_dependencies_report_ollama_and_its_models() -> None:
    """With Ollama up and its model pulled, the report is ok and lists the digest."""
    body = probed(ServiceHealth(dependencies=ollama_up)).get("/health/dependencies").json()
    assert body["status"] == "ok"
    assert body["ollama"]["reachable"] is True
    assert body["ollama"]["models"] == [{"name": "qwen2.5:3b", "present": True, "digest": "d1"}]


def test_an_ollama_outage_is_degraded_but_the_probes_still_pass() -> None:
    """An outage shows only in the dependency report: liveness and readiness stay 200."""
    client = probed(ServiceHealth(dependencies=ollama_down))
    report = client.get("/health/dependencies")
    assert report.status_code == status.HTTP_200_OK
    assert report.json()["status"] == "degraded"
    assert report.json()["ollama"]["error"] == "connection refused"
    assert client.get("/livez").status_code == status.HTTP_200_OK
    assert client.get("/readyz").status_code == status.HTTP_200_OK


def test_dependencies_without_a_check_report_no_ollama() -> None:
    """A service built around a fake model has nothing to ask, and says so."""
    body = probed(ServiceHealth()).get("/health/dependencies").json()
    assert body == {"status": "ok", "ollama": None, "retrieval": False, "history_size": 0}


def test_health_keeps_its_response_when_dependencies_are_down() -> None:
    """The existing /health answer is unchanged by the new checks."""
    body = probed(ServiceHealth(NotReady(), ollama_down)).get("/health").json()
    assert body == {"status": "ok", "retrieval": False, "history_size": 0}


@dataclass
class Wiring:
    """What main built, recorded instead of reaching Ollama.

    Attributes:
        providers: The keyword arguments each OllamaProvider was built with.
        embedders: The embedder each history index was built with.
        checks: The host and models each dependency check was asked about.
        apps: The apps main would have served.
    """

    providers: list[dict[str, Any]] = field(default_factory=list)
    embedders: list[Any] = field(default_factory=list)
    checks: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    apps: list[Any] = field(default_factory=list)

    def serve(self, app: Any, host: str, port: int) -> None:
        """Record the app instead of serving it."""
        del host, port
        self.apps.append(app)


@pytest.fixture(name="wiring")
def fixture_wiring(monkeypatch: pytest.MonkeyPatch) -> Wiring:
    """Replace everything main would send to Ollama with recorders."""
    recorded = Wiring()

    def provider(**kwargs: Any) -> FakeProvider:
        """Record how the answering model was configured."""
        recorded.providers.append(kwargs)
        return FakeProvider([CABLE_REPLY])

    def index(history: Path, embedder: Any) -> InMemoryRetriever:
        """Record the embedder instead of embedding the history."""
        del history
        recorded.embedders.append(embedder)
        return empty_history()

    def check(host: str, models: Any, *_: Any, **__: Any) -> OllamaStatus:
        """Record what the dependency check would have asked."""
        recorded.checks.append((host, tuple(models)))
        return ollama_up()

    monkeypatch.setattr("triage.api.OllamaProvider", provider)
    monkeypatch.setattr("triage.api.history_index", index)
    monkeypatch.setattr("triage.api.ollama_status", check)
    return recorded


SETTINGS = {
    "TRIAGE_OLLAMA_HOST": "http://gpu.lan:11434",
    "TRIAGE_MODEL": "qwen2.5:7b",
    "TRIAGE_EMBED_MODEL": "mxbai-embed-large",
}
FLAGS = ["--ollama-host", "http://flag.lan:1", "--model", "m-flag", "--embed-model", "e-flag"]


@pytest.mark.parametrize(
    ("arguments", "environ", "expected"),
    [
        ([], {}, (DEFAULT_HOST, DEFAULT_MODEL, DEFAULT_EMBED_MODEL)),
        ([], SETTINGS, ("http://gpu.lan:11434", "qwen2.5:7b", "mxbai-embed-large")),
        (FLAGS, SETTINGS, ("http://flag.lan:1", "m-flag", "e-flag")),
        ([], dict.fromkeys(SETTINGS, ""), (DEFAULT_HOST, DEFAULT_MODEL, DEFAULT_EMBED_MODEL)),
        ([], {"OLLAMA_HOST": "0.0.0.0:11434"}, (DEFAULT_HOST, DEFAULT_MODEL, DEFAULT_EMBED_MODEL)),
    ],
    ids=["defaults", "environment", "flags-beat-environment", "empty-is-unset", "server-var"],
)
def test_ollama_settings_reach_the_model_embedder_and_check(
    tmp_path: Path,
    wiring: Wiring,
    arguments: list[str],
    environ: dict[str, str],
    expected: tuple[str, str, str],
) -> None:
    """A flag beats TRIAGE_*, which beats the default; Ollama's own OLLAMA_HOST is ignored."""
    host, model, embed_model = expected
    assert main([*arguments, "--history", str(tmp_path)], wiring.serve, environ) == 0
    assert (wiring.providers[0]["host"], wiring.providers[0]["model"]) == (host, model)
    assert wiring.embedders == [OllamaEmbedder(model=embed_model, host=host)]
    TestClient(wiring.apps[0]).get("/health/dependencies")
    assert wiring.checks == [(host, (model, embed_model))]


def test_without_a_history_only_the_answering_model_is_checked(wiring: Wiring) -> None:
    """With --no-history no embedder is built, so the embedding model isn't required."""
    assert main(["--no-history"], wiring.serve, {}) == 0
    assert wiring.embedders == []
    TestClient(wiring.apps[0]).get("/health/dependencies")
    assert wiring.checks == [(DEFAULT_HOST, (DEFAULT_MODEL,))]


@pytest.mark.parametrize("host", ["localhost:11434", "file:///etc/passwd", "ftp://x", "http://"])
def test_an_unusable_ollama_host_stops_start_up_in_one_line(
    tmp_path: Path, wiring: Wiring, capsys: pytest.CaptureFixture[str], host: str
) -> None:
    """A host that isn't an http(s) URL exits 1 before the history is loaded or served."""
    environ = {"TRIAGE_OLLAMA_HOST": host}
    assert main(["--history", str(tmp_path)], wiring.serve, environ) == 1
    output = capsys.readouterr().out.strip().splitlines()
    assert len(output) == 1
    assert "TRIAGE_OLLAMA_HOST" in output[0]
    assert (wiring.embedders, wiring.apps) == ([], [])
