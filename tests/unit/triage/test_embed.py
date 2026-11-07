"""Tests for the embedder: the fake used offline, and the Ollama adapter against a fake server."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import numpy as np
import pytest

from tests.unit.triage.test_llm import BAD_HOSTS
from tests.utilities.tcp import closed_port, serving_in_background
from triage.embed import FAKE_WIDTH, FakeEmbedder, OllamaEmbedder
from triage.llm import ModelUnavailableError


def test_the_fake_embedder_is_fixed_width_whatever_the_text() -> None:
    """Every vector has the same width, so an index and a later query always line up."""
    embedder = FakeEmbedder()
    vectors = embedder.embed(["cable loss high", "a completely different sentence entirely"])
    assert {len(vector) for vector in vectors} == {FAKE_WIDTH}


def test_shared_words_make_closer_vectors() -> None:
    """Texts that share words sit nearer than texts that don't."""
    embedder = FakeEmbedder()
    vectors = embedder.embed(["cable loss at one ghz", "cable loss at two ghz", "firmware changed"])
    near = float(np.dot(vectors[0], vectors[1]))
    far = float(np.dot(vectors[0], vectors[2]))
    assert near > far


class _EmbedHandler(BaseHTTPRequestHandler):
    """Answers /api/embeddings with the server's fixed vector."""

    # pylint: disable=invalid-name  # do_POST is the name BaseHTTPRequestHandler needs
    def do_POST(self) -> None:
        """Answer one embedding request."""
        assert isinstance(self.server, FakeOllamaEmbed)
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(self.server.status)
        self.end_headers()
        self.wfile.write(self.server.body.encode())

    def log_message(self, format: str, *args: Any) -> None:
        """Stay quiet."""


class FakeOllamaEmbed(ThreadingHTTPServer):
    """An Ollama embeddings endpoint that answers one fixed way."""

    daemon_threads = True

    def __init__(self, body: str = json.dumps({"embedding": [0.1, 0.2, 0.3]}), status: int = 200):
        """Listen on a free local port."""
        super().__init__(("127.0.0.1", 0), _EmbedHandler)
        self.body = body
        self.status = status


def test_the_ollama_embedder_returns_the_vector() -> None:
    """A normal reply becomes a list of floats."""
    with serving_in_background(FakeOllamaEmbed()) as port:
        embedder = OllamaEmbedder(host=f"http://127.0.0.1:{port}")
        assert embedder.embed(["text"]) == [[0.1, 0.2, 0.3]]


def test_a_reply_without_an_embedding_is_refused() -> None:
    """An answer in the wrong shape is reported, not passed on empty."""
    with (
        serving_in_background(FakeOllamaEmbed(body=json.dumps({"done": True}))) as port,
        pytest.raises(ModelUnavailableError, match="no embedding"),
    ):
        OllamaEmbedder(host=f"http://127.0.0.1:{port}").embed(["text"])


def test_an_unreachable_embedder_says_how_to_start_it() -> None:
    """Nothing listening gives a clear hint, not a raw socket error."""
    with pytest.raises(ModelUnavailableError, match="ollama serve"):
        OllamaEmbedder(host=f"http://127.0.0.1:{closed_port()}").embed(["text"])


@pytest.mark.parametrize("host", BAD_HOSTS)
def test_a_non_http_host_is_refused(host: str) -> None:
    """Any host that isn't an http(s) URL is refused before anything is opened."""
    with pytest.raises(ModelUnavailableError, match="non-http"):
        OllamaEmbedder(host=host).embed(["text"])
