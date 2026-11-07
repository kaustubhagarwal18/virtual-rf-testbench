"""Tests for the answering model's adapter, against a fake Ollama rather than a real one."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from tests.utilities.tcp import closed_port, serving_in_background
from triage.llm import FakeProvider, ModelUnavailableError, OllamaProvider

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
