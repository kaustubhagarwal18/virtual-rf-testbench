"""Tests for the answering model's adapter, against a fake Ollama rather than a real one."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests.utilities.tcp import closed_port, serving_in_background
from triage.answer import VERDICT_SCHEMA
from triage.llm import (
    DEFAULT_MODEL,
    MAX_REPLY_TOKENS,
    CachingProvider,
    FakeProvider,
    ModelUnavailableError,
    OllamaProvider,
)
from triage.pack import DEFAULT_PACK

ANSWER = {"message": {"content": '{"fix": "CABLE"}'}}


class _Handler(BaseHTTPRequestHandler):
    """Answers like Ollama, with whatever the server was told to answer."""

    # pylint: disable=invalid-name  # do_GET/do_POST are the names BaseHTTPRequestHandler needs

    def do_GET(self) -> None:
        """Answer the version check, which the availability check asks for."""
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"version": "fake"}')

    def do_POST(self) -> None:
        """Record the request and answer it."""
        assert isinstance(self.server, FakeOllama)
        length = int(self.headers.get("Content-Length", "0"))
        self.server.requests.append(json.loads(self.rfile.read(length)))
        self.send_response(self.server.status)
        self.end_headers()
        self.wfile.write(self.server.body.encode())

    def log_message(self, format: str, *args: Any) -> None:
        """Stay quiet while the tests run."""


class FakeOllama(ThreadingHTTPServer):
    """An Ollama that answers one fixed way and remembers what it was asked."""

    daemon_threads = True

    def __init__(self, body: str = json.dumps(ANSWER), status: int = 200) -> None:
        """Listen on a free local port.

        Args:
            body: What to answer with.
            status: The HTTP status to answer with.
        """
        super().__init__(("127.0.0.1", 0), _Handler)
        self.body = body
        self.status = status
        self.requests: list[dict[str, Any]] = []


def test_the_request_asks_for_json_at_temperature_zero() -> None:
    """The model is asked for JSON, deterministically, with both messages in order."""
    server = FakeOllama()
    with serving_in_background(server) as port:
        provider = OllamaProvider(host=f"http://127.0.0.1:{port}")
        assert provider.complete("instructions", "question") == '{"fix": "CABLE"}'
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["format"] == "json"
    assert request["options"]["temperature"] == 0
    assert request["options"]["num_predict"] == MAX_REPLY_TOKENS
    assert [message["role"] for message in request["messages"]] == ["system", "user"]
    assert request["messages"][1]["content"] == "question"


def test_a_refused_request_says_whether_the_model_is_pulled() -> None:
    """A 404 from Ollama usually means the model was never pulled, so the error says so."""
    with serving_in_background(FakeOllama(body="not found", status=404)) as port:
        provider = OllamaProvider(host=f"http://127.0.0.1:{port}")
        with pytest.raises(ModelUnavailableError, match=re.escape("is qwen2.5:3b pulled")):
            provider.complete("instructions", "question")


def test_an_unreachable_ollama_says_how_to_start_it() -> None:
    """With nothing listening, the error names the command that fixes it."""
    provider = OllamaProvider(host=f"http://127.0.0.1:{closed_port()}")
    with pytest.raises(ModelUnavailableError, match="ollama serve"):
        provider.complete("instructions", "question")
    assert not provider.available()


def test_a_reply_without_content_is_refused() -> None:
    """An answer in the wrong shape is reported, not passed on as an empty reply."""
    with serving_in_background(FakeOllama(body=json.dumps({"done": True}))) as port:
        provider = OllamaProvider(host=f"http://127.0.0.1:{port}")
        with pytest.raises(ModelUnavailableError, match="no reply content"):
            provider.complete("instructions", "question")


def test_a_reply_that_isnt_json_is_refused() -> None:
    """Ollama answering HTML, as a proxy might, is reported clearly."""
    with serving_in_background(FakeOllama(body="<html>502</html>")) as port:
        provider = OllamaProvider(host=f"http://127.0.0.1:{port}")
        with pytest.raises(ModelUnavailableError, match="didn't answer with JSON"):
            provider.complete("instructions", "question")


def test_a_running_ollama_is_available() -> None:
    """The availability check is what lets a test skip instead of failing."""
    with serving_in_background(FakeOllama()) as port:
        assert OllamaProvider(host=f"http://127.0.0.1:{port}").available()


def test_the_fake_provider_answers_in_order_and_records_the_prompts() -> None:
    """The fake gives each reply once, repeats the last, and keeps what it was asked."""
    fake = FakeProvider(["first", "second"])
    assert [fake.complete("s", "a"), fake.complete("s", "b"), fake.complete("s", "c")] == [
        "first",
        "second",
        "second",
    ]
    assert [user for _system, user in fake.prompts] == ["a", "b", "c"]


BAD_HOSTS = (
    "file:///etc/passwd",
    "ftp://127.0.0.1",
    "localhost",
    "127.0.0.1",
    "localhost:11434",
    "",
)
"""Hosts a str allows that aren't an http(s) URL: a custom scheme, or no scheme at all."""


@pytest.mark.parametrize("host", BAD_HOSTS)
def test_a_non_http_host_is_refused(host: str) -> None:
    """Any host that isn't an http(s) URL is refused with one clear error, never a traceback."""
    with pytest.raises(ModelUnavailableError, match="non-http"):
        OllamaProvider(host=host).complete("s", "u")


@pytest.mark.parametrize("host", BAD_HOSTS)
def test_a_non_http_host_is_reported_unavailable(host: str) -> None:
    """The availability check treats a misconfigured host as not available, not an error."""
    assert not OllamaProvider(host=host).available()


def test_a_schema_is_sent_as_the_reply_format() -> None:
    """With a schema, Ollama is asked to constrain the reply to it, not just to any JSON."""
    server = FakeOllama()
    with serving_in_background(server) as port:
        provider = OllamaProvider(host=f"http://127.0.0.1:{port}", response_format=VERDICT_SCHEMA)
        provider.complete("instructions", "question")
    assert server.requests[0]["format"] == VERDICT_SCHEMA
    assert server.requests[0]["format"]["properties"]["fix"]["enum"] == [
        fix.key for fix in DEFAULT_PACK.actions
    ]


def test_the_cache_asks_each_distinct_prompt_once() -> None:
    """The same prompt is answered from the cache; a different one reaches the model."""
    inner = FakeProvider(["first", "second"])
    cached = CachingProvider(inner)
    assert cached.complete("s", "u") == "first"
    assert cached.complete("s", "u") == "first"
    assert cached.complete("s", "other") == "second"
    assert inner.prompts == [("s", "u"), ("s", "other")]


def test_the_cache_names_the_inner_model() -> None:
    """A report headed by the cache's model names the model that actually answered."""
    assert CachingProvider(OllamaProvider(model="qwen2.5:3b")).model == "qwen2.5:3b"
    assert CachingProvider(FakeProvider()).model == DEFAULT_MODEL


class IdentifiedFake(FakeProvider):
    """A fake model that names its identity, so its answers can be stored on disk."""

    def __init__(self, replies: list[str], model: str = "fake-3b") -> None:
        """Answer from the list, as the given model."""
        super().__init__(replies)
        self.model = model

    def identity(self) -> dict[str, object]:
        """Say which model this is."""
        return {"model": self.model}


def test_stored_answers_are_reused_by_a_later_run(tmp_path: Path) -> None:
    """A second run with the same model and prompt asks the model nothing."""
    first = CachingProvider(IdentifiedFake(["stored"]), tmp_path)
    assert first.complete("s", "u") == "stored"
    later_model = IdentifiedFake(["never asked"])
    later = CachingProvider(later_model, tmp_path)
    assert later.complete("s", "u") == "stored"
    assert (first.asked, later.asked, later_model.prompts) == (1, 0, [])


@pytest.mark.parametrize(
    ("model", "system", "user"),
    [("other-7b", "s", "u"), ("fake-3b", "other instructions", "u"), ("fake-3b", "s", "other")],
)
def test_a_different_model_or_prompt_is_asked_again(
    tmp_path: Path, model: str, system: str, user: str
) -> None:
    """Changing the model, the instructions or the question misses the stored answer."""
    CachingProvider(IdentifiedFake(["stored"]), tmp_path).complete("s", "u")
    later = CachingProvider(IdentifiedFake(["fresh"], model), tmp_path)
    assert later.complete(system, user) == "fresh"
    assert later.asked == 1


@pytest.mark.parametrize("damage", ["not json", "[]", '{"reply": "x"}', "{}"])
def test_a_damaged_or_mismatched_file_is_ignored(tmp_path: Path, damage: str) -> None:
    """A stored file that doesn't parse or doesn't match the prompt exactly is never used."""
    CachingProvider(IdentifiedFake(["stored"]), tmp_path).complete("s", "u")
    (stored,) = tmp_path.glob("*.json")
    stored.write_text(damage, encoding="utf-8")
    assert CachingProvider(IdentifiedFake(["fresh"]), tmp_path).complete("s", "u") == "fresh"


def test_a_stored_answer_leaves_no_partial_file(tmp_path: Path) -> None:
    """Answers are written whole: one file per answer, and no partial file left behind."""
    cached = CachingProvider(IdentifiedFake(["a", "b"]), tmp_path)
    cached.complete("s", "u1")
    cached.complete("s", "u2")
    assert sorted(path.suffix for path in tmp_path.iterdir()) == [".json", ".json"]
    assert not list(tmp_path.glob("*.partial"))


def test_storing_answers_needs_an_identity(tmp_path: Path) -> None:
    """A provider that can't say which model it is can't have its answers stored."""
    with pytest.raises(ValueError, match="has no identity"):
        CachingProvider(FakeProvider(), tmp_path)


def test_the_ollama_identity_names_everything_that_decides_an_answer() -> None:
    """Model, temperature, seed, context, reply cap and format each change the key."""
    identity = OllamaProvider(model="qwen2.5:3b", response_format=VERDICT_SCHEMA).identity()
    assert set(identity) == {"model", "temperature", "seed", "num_ctx", "num_predict", "format"}
    assert identity["format"] == VERDICT_SCHEMA


def test_a_schema_for_one_reply_replaces_the_default() -> None:
    """A schema passed with the prompt is what Ollama is asked to follow, for that reply only."""
    server = FakeOllama()
    narrow: dict[str, object] = {"type": "object", "properties": {"cited": {"enum": ["a"]}}}
    with serving_in_background(server) as port:
        provider = OllamaProvider(host=f"http://127.0.0.1:{port}", response_format=VERDICT_SCHEMA)
        provider.complete("instructions", "question", narrow)
        provider.complete("instructions", "question")
    assert [request["format"] for request in server.requests] == [narrow, VERDICT_SCHEMA]


def test_a_different_schema_is_a_different_question(tmp_path: Path) -> None:
    """The same prompts under another schema are asked again; under the same one, not."""
    narrow: dict[str, object] = {"cited": ["past-1"]}
    cached = CachingProvider(IdentifiedFake(["default", "narrow"]), tmp_path)
    assert cached.complete("s", "u") == "default"
    assert cached.complete("s", "u", narrow) == "narrow"
    assert cached.complete("s", "u", narrow) == "narrow"
    later = CachingProvider(IdentifiedFake(["never asked"]), tmp_path)
    assert (later.complete("s", "u"), later.complete("s", "u", narrow)) == ("default", "narrow")
    assert (cached.asked, later.asked) == (2, 0)


def test_an_answer_stored_without_a_schema_keeps_its_key(tmp_path: Path) -> None:
    """A prompt with the default schema is stored as before, so earlier answers still match."""
    CachingProvider(IdentifiedFake(["stored"]), tmp_path).complete("s", "u")
    stored = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert set(stored) == {"identity", "system", "user", "reply"}


def test_the_fake_records_each_prompts_schema() -> None:
    """A test can see which schema each prompt was asked under."""
    fake = FakeProvider(["a"])
    fake.complete("s", "u")
    fake.complete("s", "u", {"x": 1})
    assert fake.schemas == [None, {"x": 1}]
