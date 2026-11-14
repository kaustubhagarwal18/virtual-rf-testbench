"""A stand-in for Ollama's HTTP API, for the container tests: standard library only.

It answers the three requests the triage service makes before it is ready, the way Ollama
documents them:

- `GET /api/version`: a version.
- `GET /api/tags`: each configured model's name and digest.
- `POST /api/embeddings`: a deterministic vector for the prompt, from its SHA-256, so the
  same text always gets the same vector and a cached index matches a fresh one.

Anything else is a 404, so a test never gets an answer it didn't plan for. It runs inside a
container started from the triage image, with this file mounted read-only:

    python /fake/fake_ollama.py

Settings come from the environment, so a test changes them by starting a new container:
FAKE_OLLAMA_MODELS (comma-separated names as /api/tags lists them), FAKE_OLLAMA_EMBED_DIGEST
(the digest every model is listed with), FAKE_OLLAMA_EMBED_DELAY_S (how long each embedding
takes, so a test can watch the service while it re-embeds), and FAKE_OLLAMA_EMBED_BARRIER
(set to 1 to block every embedding until GET /barrier/release is called, so a test can
reliably observe the not-ready state before the re-embed completes).
"""

import hashlib
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

PORT = 11434
"""Ollama's own port, so the service's settings look like a real deployment's."""

VERSION = "0.0.0-fake"
"""The version /api/version reports: plainly not a real Ollama."""

DEFAULT_MODELS = "qwen2.5:3b,nomic-embed-text:latest"
"""The models listed when FAKE_OLLAMA_MODELS isn't set: the service's defaults."""

DEFAULT_DIGEST = "sha256:" + "0" * 64
"""The digest listed when FAKE_OLLAMA_EMBED_DIGEST isn't set."""

VECTOR_WIDTH = 32
"""The width of every vector: one per byte of the prompt's SHA-256."""

_LOG = logging.getLogger("fake_ollama")


@dataclass(frozen=True)
class FakeSettings:
    """What the fake reports.

    Attributes:
        models: The model names /api/tags lists.
        digest: The digest each model is listed with.
        embed_delay_s: How long each embedding takes.
        embed_barrier: When True, every embedding blocks until GET /barrier/release is called.
    """

    models: tuple[str, ...]
    digest: str
    embed_delay_s: float
    embed_barrier: bool

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> "FakeSettings":
        """Read the settings from the environment, with a default for each unset one.

        Args:
            environ: The environment.

        Returns:
            The settings.
        """
        names = environ.get("FAKE_OLLAMA_MODELS") or DEFAULT_MODELS
        return cls(
            models=tuple(name.strip() for name in names.split(",") if name.strip()),
            digest=environ.get("FAKE_OLLAMA_EMBED_DIGEST") or DEFAULT_DIGEST,
            embed_delay_s=float(environ.get("FAKE_OLLAMA_EMBED_DELAY_S") or 0),
            embed_barrier=environ.get("FAKE_OLLAMA_EMBED_BARRIER") == "1",
        )


def vector_for(text: str) -> list[float]:
    """Turn a text into a fixed-width vector that depends only on the text.

    Args:
        text: The text to embed.

    Returns:
        VECTOR_WIDTH numbers between -1 and 1, one per byte of the text's SHA-256.
    """
    return [(byte - 127.5) / 127.5 for byte in hashlib.sha256(text.encode("utf-8")).digest()]


class FakeOllamaHandler(BaseHTTPRequestHandler):
    """Answers the Ollama requests the triage service makes; everything else is a 404."""

    settings: ClassVar[FakeSettings] = FakeSettings.from_environment({})
    # Starts set (unblocked); main() clears it when embed_barrier is on.
    embed_gate: ClassVar[threading.Event] = threading.Event()

    def do_GET(self) -> None:  # pylint: disable=invalid-name
        """Answer /api/version, /api/tags, and /barrier/release."""
        if self.path == "/api/version":
            self._reply(HTTPStatus.OK, {"version": VERSION})
        elif self.path == "/api/tags":
            models = [
                {"name": name, "digest": self.settings.digest} for name in self.settings.models
            ]
            self._reply(HTTPStatus.OK, {"models": models})
        elif self.path == "/barrier/release":
            self.embed_gate.set()
            self._reply(HTTPStatus.OK, {"released": True})
        else:
            self._reply(HTTPStatus.NOT_FOUND, {"error": f"no route {self.path}"})

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        """Answer /api/embeddings with the prompt's deterministic vector."""
        if self.path != "/api/embeddings":
            self._reply(HTTPStatus.NOT_FOUND, {"error": f"no route {self.path}"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            prompt = json.loads(self.rfile.read(length))["prompt"]
        except (ValueError, KeyError, TypeError) as error:
            self._reply(HTTPStatus.BAD_REQUEST, {"error": f"unusable request: {error}"})
            return
        self.embed_gate.wait()
        time.sleep(self.settings.embed_delay_s)
        self._reply(HTTPStatus.OK, {"embedding": vector_for(str(prompt))})

    def log_message(self, format: str, *args: Any) -> None:
        """Log each request as one JSON line instead of http.server's plain text.

        Args:
            format: The message's format string (matches the base class signature).
            *args: Its arguments.
        """
        _LOG.info(json.dumps({"event": "request", "line": format % args}))

    def _reply(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        """Send a JSON reply.

        Args:
            status: The status code.
            body: The reply's body.
        """
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> int:
    """Serve on every interface until stopped.

    Returns:
        0 once the server stops.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    FakeOllamaHandler.settings = FakeSettings.from_environment(os.environ)
    if FakeOllamaHandler.settings.embed_barrier:
        # Clear the gate so embeddings block until GET /barrier/release sets it.
        FakeOllamaHandler.embed_gate.clear()
    # Inside a container on a private test network: every interface is that network.
    server = ThreadingHTTPServer(("0.0.0.0", PORT), FakeOllamaHandler)  # nosec B104
    _LOG.info(json.dumps({"event": "listening", "port": PORT, **vars(FakeOllamaHandler.settings)}))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
