"""The triage image as it is deployed: a read-only container, a volume, an Ollama that fails.

Each test answers one operational question the unit tests can't, because they need the real
image, its volume and its process lifecycle:

- Does the history survive the container being replaced?
- Does an Ollama outage stay out of the probes, so nothing restarts a healthy process?
- Does a restart while Ollama is down come up from the cached vectors?
- Does a changed embedding model at start-up get re-embedded before the service is ready?
"""

import time
from http import HTTPStatus
from typing import Any

from tests.container.conftest import DIGEST_A, DIGEST_B, ContainerBench, TriageContainer

FAILING_TEST: dict[str, Any] = {
    "id": "tests/bench/test_level.py::test_level_at_1ghz",
    "outcome": "failed",
    "message": "Failed: level_1000mhz: -13.0 dBm, limit -12 to -11 dBm, margin -1.0 dB, FAIL",
    "measurements": [
        {
            "name": "level_1000mhz",
            "value": -13.0,
            "unit": "dBm",
            "limit_low": -12.0,
            "limit_high": -11.0,
            "margin": -1.0,
            "passed": False,
        }
    ],
}
"""One failed level test, shaped as the bench suite's results file records it."""

RESOLVED_RUN: dict[str, Any] = {
    "results": {"tests": [FAILING_TEST]},
    "label": {
        "fault": "cable_loss",
        "cause": "a worn cable between the generator and the analyzer",
        "fix": "replaced the cable",
        "action": "CABLE",
    },
}
"""A resolved run to add to the history over HTTP."""

DIP_EMBED_DELAY_S = 0.2
"""How long each embedding takes after the digest change: long enough to see the re-embed."""

DIP_TIMEOUT_S = 90.0
"""How long the service may take to come back after the digest change."""


def _start_verified(bench: ContainerBench, name: str) -> TriageContainer:
    """Start the service and wait until it is ready with its digest confirmed by Ollama.

    Args:
        bench: The test's bench, with the fake Ollama running.
        name: A name for the container.

    Returns:
        The ready service.
    """
    service = bench.start_triage(name)
    service.wait_verified()
    return service


def test_an_added_run_survives_the_container_being_replaced(bench: ContainerBench) -> None:
    """A run posted to /history is still there after rm -f and a new container on the volume."""
    bench.start_fake_ollama()
    first = _start_verified(bench, "first")
    added = first.post("/history", RESOLVED_RUN)
    assert added.status == HTTPStatus.CREATED, added
    bench.remove(first)
    second = bench.start_triage("second")
    second.wait_ready()
    assert second.store()["stored_runs"] == 1
    assert second.get("/health").body["history_size"] == added.body["history_size"]
    again = second.post("/history", RESOLVED_RUN)
    assert again.status == HTTPStatus.CREATED, again
    assert again.body == added.body


def test_an_ollama_outage_stays_out_of_the_probes(bench: ContainerBench) -> None:
    """With Ollama gone, the probes stay 200, /triage is 503 and nothing restarts."""
    bench.start_fake_ollama()
    service = _start_verified(bench, "outage")
    bench.stop_fake_ollama()
    assert service.get("/livez").status == HTTPStatus.OK
    assert service.get("/readyz").status == HTTPStatus.OK
    triaged = service.post("/triage", {"results": {"tests": [FAILING_TEST]}})
    assert triaged.status == HTTPStatus.SERVICE_UNAVAILABLE, triaged
    report = service.get("/health/dependencies")
    assert report.status == HTTPStatus.OK
    assert report.body["status"] == "degraded"
    assert report.body["ollama"]["reachable"] is False
    assert service.inspect("{{.RestartCount}}") == "0"
    assert service.inspect("{{.State.Running}}") == "true"


def test_a_restart_with_ollama_down_comes_up_from_the_cache(bench: ContainerBench) -> None:
    """A new container on a warm volume is ready with Ollama down, its digest unverified."""
    bench.start_fake_ollama()
    warm = _start_verified(bench, "warm")
    bench.remove(warm)
    bench.stop_fake_ollama()
    cold = bench.start_triage("offline")
    cold.wait_ready()
    store = cold.store()
    assert store["digest"] == DIGEST_A
    assert store["digest_verified"] is False
    assert store["cache_misses"] == 0
    assert store["cache_hits"] > 0
    assert cold.get("/health/dependencies").body["status"] == "degraded"


def test_a_changed_digest_at_start_up_is_re_embedded_before_ready(
    bench: ContainerBench,
) -> None:
    """After the model's digest changes, a restart is not ready until it has re-embedded."""
    bench.start_fake_ollama(digest=DIGEST_A)
    service = _start_verified(bench, "digest")
    assert service.post("/history", RESOLVED_RUN).status == HTTPStatus.CREATED
    bench.stop_fake_ollama()
    # The barrier holds every embedding until release_fake_ollama_barrier() is called,
    # so the loop below is guaranteed to observe 503 before re-embedding can finish.
    bench.start_fake_ollama(digest=DIGEST_B, embed_delay_s=DIP_EMBED_DELAY_S, barrier=True)
    service.restart()
    seen: list[int] = []
    deadline = time.monotonic() + DIP_TIMEOUT_S
    while HTTPStatus.SERVICE_UNAVAILABLE not in seen:
        assert time.monotonic() < deadline, (
            f"service did not become not-ready within {DIP_TIMEOUT_S:g} s: {seen[-5:]}"
        )
        seen.append(service.get("/readyz").status)
    bench.release_fake_ollama_barrier()
    while not seen or seen[-1] != HTTPStatus.OK:
        assert time.monotonic() < deadline, f"not ready within {DIP_TIMEOUT_S:g} s: {seen[-5:]}"
        seen.append(service.get("/readyz").status)
    store = service.store()
    assert store["digest"] == DIGEST_B
    assert store["digest_verified"] is True
    assert store["cache_hits"] == 0
    assert store["cache_misses"] > 0
    assert store["stored_runs"] == 1
