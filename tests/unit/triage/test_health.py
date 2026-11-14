"""Tests for the dependency check and readiness, with a fake opener: nothing is sent."""

import json
import urllib.error

import pytest

from triage.health import (
    ALWAYS_READY,
    AlwaysReady,
    ModelStatus,
    OllamaStatus,
    full_model_name,
    ollama_status,
)
from triage.llm import ModelUnavailableError

HOST = "http://ollama.test:11434"
DIGEST_3B = "357c53fb659c"
DIGEST_EMBED = "0a109f422b47"
TAGS = {
    "models": [
        {"name": "qwen2.5:3b", "model": "qwen2.5:3b", "digest": DIGEST_3B},
        {"name": "nomic-embed-text:latest", "digest": DIGEST_EMBED},
    ]
}
"""What /api/tags returns, in the shape seen from a real Ollama, trimmed to the fields used."""


class FakeOllama:
    """An opener that answers like Ollama and records what was asked."""

    def __init__(self, replies: dict[str, bytes]) -> None:
        """Answer each path with its reply.

        Args:
            replies: The body for each path, such as /api/tags.
        """
        self.replies = replies
        self.asked: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout_s: float) -> bytes:
        """Return the reply for the URL's path."""
        self.asked.append((url, timeout_s))
        return self.replies[url.removeprefix(HOST)]


def answering(tags: object = None) -> FakeOllama:
    """Return a fake Ollama that is up, with the given model list or TAGS."""
    return FakeOllama(
        {
            "/api/version": json.dumps({"version": "0.40.14"}).encode(),
            "/api/tags": json.dumps(TAGS if tags is None else tags).encode(),
        }
    )


def test_a_reachable_ollama_lists_each_models_digest() -> None:
    """Both models are found with their digests, asked at the host with a short timeout."""
    opener = answering()
    found = ollama_status(HOST, ["qwen2.5:3b", "nomic-embed-text"], timeout_s=2.0, opener=opener)
    assert found.reachable and found.healthy
    assert found.version == "0.40.14"
    assert found.error is None
    assert found.models == (
        ModelStatus("qwen2.5:3b", present=True, digest=DIGEST_3B),
        ModelStatus("nomic-embed-text", present=True, digest=DIGEST_EMBED),
    )
    assert opener.asked == [(f"{HOST}/api/version", 2.0), (f"{HOST}/api/tags", 2.0)]


@pytest.mark.parametrize(
    ("name", "listed"),
    [("nomic-embed-text", "nomic-embed-text:latest"), ("qwen2.5:3b", "qwen2.5:3b")],
)
def test_a_model_without_a_tag_is_matched_as_latest(name: str, listed: str) -> None:
    """Ollama lists a model pulled without a tag under :latest; a tagged name is kept."""
    assert full_model_name(name) == listed


def test_a_model_that_isnt_pulled_is_reported_missing() -> None:
    """Ollama is up but lacks a configured model: reachable, not healthy, no digest."""
    found = ollama_status(HOST, ["qwen2.5:7b"], opener=answering())
    assert found.reachable
    assert not found.healthy
    assert found.models == (ModelStatus("qwen2.5:7b", present=False, digest=None),)


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("connection refused"),
        TimeoutError("timed out"),
        ModelUnavailableError("refusing to open a non-http(s) URL"),
        b"not json",
        b"[1, 2]",
    ],
    ids=["refused", "timeout", "bad-url", "not-json", "not-an-object"],
)
def test_any_failure_is_reported_not_raised(failure: Exception | bytes) -> None:
    """Every way the check can fail comes back as unreachable with a reason; nothing raises."""

    def opener(url: str, timeout_s: float) -> bytes:
        """Fail, or answer with something that isn't a JSON object."""
        del url, timeout_s
        if isinstance(failure, bytes):
            return failure
        raise failure

    found = ollama_status(HOST, ["qwen2.5:3b"], opener=opener)
    assert not found.reachable and not found.healthy
    assert found.error
    assert found.models == (ModelStatus("qwen2.5:3b", present=False, digest=None),)


@pytest.mark.parametrize(
    "tags",
    [{"models": None}, {"models": "qwen2.5:3b"}, {"models": {"name": "qwen2.5:3b"}}, {"models": 3}],
    ids=["null", "string", "object", "number"],
)
def test_a_model_list_of_the_wrong_shape_is_reported_not_raised(tags: dict[str, object]) -> None:
    """A tags reply whose models isn't a list is unreachable with a reason, never a crash."""

    def opener(url: str, timeout_s: float) -> bytes:
        """Answer the version request normally and the tags request with the odd shape."""
        del timeout_s
        reply: object = {"version": "0.40.14"} if url.endswith("/api/version") else tags
        return json.dumps(reply).encode()

    found = ollama_status(HOST, ["qwen2.5:3b"], opener=opener)
    assert not found.reachable and not found.healthy
    assert found.error is not None and "not a list" in found.error
    assert found.models == (ModelStatus("qwen2.5:3b", present=False, digest=None),)


def test_an_entry_without_a_string_name_is_skipped() -> None:
    """A listed model with no name, or a non-string one, can't be mistaken for a configured one."""
    tags = {"models": [{"digest": "abc"}, {"name": None, "digest": "def"}, {"name": 7}]}

    def opener(url: str, timeout_s: float) -> bytes:
        """Answer both requests; the tags carry only unnamed entries."""
        del timeout_s
        reply: object = {"version": "0.40.14"} if url.endswith("/api/version") else tags
        return json.dumps(reply).encode()

    found = ollama_status(HOST, ["None"], opener=opener)
    assert found.reachable
    assert found.models == (ModelStatus("None", present=False, digest=None),)


def test_a_host_without_a_scheme_is_refused_before_anything_is_sent() -> None:
    """With the real opener, a schemeless host fails at the scheme check, not on the network."""
    found = ollama_status("localhost:11434", ["qwen2.5:3b"])
    assert not found.reachable
    assert found.error is not None and "non-http(s)" in found.error


def test_an_odd_model_list_entry_is_ignored() -> None:
    """An entry that isn't an object can't break the check; the real models still count."""
    tags = {"models": ["junk", *TAGS["models"]]}
    found = ollama_status(HOST, ["qwen2.5:3b"], opener=answering(tags))
    assert found.healthy


def test_always_ready_has_no_reason_not_to_serve() -> None:
    """A service whose history was loaded before serving is always ready."""
    assert AlwaysReady().not_ready_reason() is None
    assert ALWAYS_READY.not_ready_reason() is None


def test_healthy_needs_ollama_up_and_every_model_present() -> None:
    """Reachable with every model is healthy; one missing model or no answer is not."""
    present = ModelStatus("a", present=True, digest="d")
    missing = ModelStatus("b", present=False, digest=None)
    assert OllamaStatus(HOST, True, "v", None, (present,)).healthy
    assert not OllamaStatus(HOST, True, "v", None, (present, missing)).healthy
    assert not OllamaStatus(HOST, False, None, "down", ()).healthy
