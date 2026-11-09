"""The answering model behind one small interface, so the pipeline never depends on Ollama.

The unit tests pass a fake, which is what keeps them offline, fast and repeatable, and lets
them produce replies a real model would rarely give: truncated JSON, an invented citation,
an action that isn't on the list. Only the evaluation and one marked integration test talk
to Ollama.
"""

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

DEFAULT_MODEL = "qwen2.5:3b"
"""The largest model that runs comfortably on an 8 GB machine (see the Ollama spike)."""

DEFAULT_HOST = "http://127.0.0.1:11434"
"""Ollama listens here, on this machine only."""

DEFAULT_TIMEOUT_S = 300.0
"""How long one answer may take; a 3B model on a CPU runner is slow."""

CONTEXT_TOKENS = 4096
"""Enough for a run's evidence and several retrieved sources."""


ALLOWED_SCHEMES = ("http", "https")
"""The host is configurable, so only http(s) is opened: never file: or a custom scheme."""


class ModelUnavailableError(Exception):
    """The model couldn't be reached; the message says how to start it."""


def open_url(url: str, timeout_s: float, payload: dict[str, Any] | None = None) -> bytes:
    """Open an http(s) URL and return its body, refusing any other scheme.

    The scheme is checked on the URL's text before a request is built, because urllib
    can't even build a request for a host with no scheme ("localhost") and would raise a
    bare ValueError instead of saying what is misconfigured.

    Args:
        url: The URL to open.
        timeout_s: How long it may take.
        payload: A JSON body to POST; None sends a GET.

    Returns:
        The response body.

    Raises:
        ModelUnavailableError: If the URL isn't http(s); a missing, file: or custom scheme
            is a misconfiguration, not a model to reach.
    """
    if urllib.parse.urlparse(url).scheme not in ALLOWED_SCHEMES:
        raise ModelUnavailableError(
            f"refusing to open a non-http(s) URL: {url!r}; set the host to a URL such as "
            f"{DEFAULT_HOST}"
        )
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    # The scheme is checked above; bandit can't see that, so B310 is silenced here.
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # nosec B310
        body: bytes = response.read()
    return body


def unreachable_message(host: str, error: object) -> str:
    """Build the one-line hint shown when a local Ollama can't be reached.

    Shared by the answering model and the embedder, so both name the same fix.

    Args:
        host: Where Ollama was expected to listen.
        error: The connection error that was raised.

    Returns:
        One line naming the host, the error, and the command that starts Ollama.
    """
    return f"can't reach Ollama at {host} ({error}); start it with 'ollama serve'"


class LLMProvider(Protocol):
    """Anything that can answer one prompt."""

    def complete(self, system: str, user: str) -> str:
        """Answer a prompt.

        Args:
            system: The instructions.
            user: The question.

        Returns:
            The model's reply, unparsed.
        """


@dataclass(frozen=True)
class OllamaProvider:
    """The answering model, running locally under Ollama.

    Attributes:
        model: Which model to ask.
        host: Where Ollama listens.
        temperature: 0 so the same question gives the same answer on one machine.
        seed: Fixed, for the same reason.
        timeout_s: How long one answer may take.
        response_format: "json" for any JSON object, or a JSON schema the reply must match
            (Ollama's structured outputs), such as triage.answer.VERDICT_SCHEMA.
    """

    model: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    temperature: float = 0.0
    seed: int = 42
    timeout_s: float = DEFAULT_TIMEOUT_S
    response_format: str | dict[str, object] = "json"

    def complete(self, system: str, user: str) -> str:
        """Ask the model, requiring JSON back, in the schema given if there is one.

        Args:
            system: The instructions.
            user: The question.

        Returns:
            The reply's content.

        Raises:
            ModelUnavailableError: If Ollama can't be reached, refuses the request, or
                answers something that isn't a chat reply.
        """
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": self.response_format,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "seed": self.seed,
                "num_ctx": CONTEXT_TOKENS,
            },
        }
        try:
            answer = json.loads(open_url(f"{self.host}/api/chat", self.timeout_s, body))
        except urllib.error.HTTPError as error:
            raise ModelUnavailableError(
                f"{self.host} refused the request ({error.code}); is {self.model} pulled?"
            ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ModelUnavailableError(unreachable_message(self.host, error)) from error
        except json.JSONDecodeError as error:
            raise ModelUnavailableError(f"{self.host} didn't answer with JSON: {error}") from error
        content = answer.get("message", {}).get("content")
        if not isinstance(content, str):
            raise ModelUnavailableError(f"no reply content in {str(answer)[:200]}")
        return content

    def identity(self) -> dict[str, object]:
        """Say everything that decides this provider's answer, apart from the prompt.

        Returns:
            The model, temperature, seed, context size and reply format.
        """
        return {
            "model": self.model,
            "temperature": self.temperature,
            "seed": self.seed,
            "num_ctx": CONTEXT_TOKENS,
            "format": self.response_format,
        }

    def available(self) -> bool:
        """Check whether Ollama is running, so a test can skip instead of failing.

        Returns:
            True if it answers.
        """
        try:
            open_url(f"{self.host}/api/version", timeout_s=2.0)
        except (urllib.error.URLError, TimeoutError, ModelUnavailableError):
            return False
        return True


@dataclass
class FakeProvider:
    """Answers from a list, and records what it was asked.

    Attributes:
        replies: The replies to give, in order; the last one repeats once they run out.
        prompts: Every (system, user) pair it was asked, for a test to check.
    """

    replies: Sequence[str] = ("{}",)
    prompts: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str) -> str:
        """Record the prompt and return the next canned reply.

        Args:
            system: The instructions.
            user: The question.

        Returns:
            The next reply, or the last one again.
        """
        self.prompts.append((system, user))
        index = min(len(self.prompts) - 1, len(self.replies) - 1)
        return self.replies[index]


class CachingProvider:
    """Answers each distinct prompt once, and repeats that answer for the same prompt.

    The model runs at temperature 0 with a fixed seed, so the same prompt gets the same
    answer; asking again would only cost time. In memory, two evaluation scenarios that
    differ only after the model has answered cost one call. With a directory, answers also
    persist between runs, so a rerun asks only the prompts that changed.

    An answer on disk is keyed by a hash of the provider's identity (model, temperature,
    seed, context size and reply format) and both prompts, and is used only if all of them
    match what it was stored with. A different model or setting is a different answer.

    Attributes:
        inner: The provider that actually answers.
        answers: The answers so far, by (system, user) prompt.
        directory: Where answers persist between runs; None keeps them in memory only.
        asked: How many prompts reached the inner provider, for a run to report.
    """

    def __init__(self, inner: LLMProvider, directory: Path | None = None) -> None:
        """Wrap a provider, with nothing cached in memory yet.

        Args:
            inner: The provider that actually answers.
            directory: Where answers persist between runs; None keeps them in memory only.

        Raises:
            ValueError: If a directory is given but the provider has no identity() to key
                the answers by: storing them under a guessed identity could replay one
                model's answers as another's.
        """
        identity = getattr(inner, "identity", None)
        if directory is not None and not callable(identity):
            raise ValueError(
                f"{type(inner).__name__} has no identity(), so its answers can't be stored"
            )
        self.inner = inner
        self.answers: dict[tuple[str, str], str] = {}
        self.directory = directory
        self.asked = 0
        self._identity: object = identity() if callable(identity) else None

    @property
    def model(self) -> str:
        """The inner provider's model name, for a report's heading.

        Returns:
            Its model, or DEFAULT_MODEL when the inner provider doesn't name one.
        """
        return str(getattr(self.inner, "model", DEFAULT_MODEL))

    def complete(self, system: str, user: str) -> str:
        """Answer from memory, then from disk, or ask the inner provider once and keep it.

        Args:
            system: The instructions.
            user: The question.

        Returns:
            The reply.
        """
        key = (system, user)
        if key not in self.answers:
            stored = self._read(system, user)
            if stored is None:
                stored = self.inner.complete(system, user)
                self.asked += 1
                self._write(system, user, stored)
            self.answers[key] = stored
        return self.answers[key]

    def _record(self, system: str, user: str) -> dict[str, Any]:
        """Describe one prompt to this provider: what an answer on disk must match.

        Args:
            system: The instructions.
            user: The question.

        Returns:
            The provider's identity and both prompts.
        """
        return {"identity": self._identity, "system": system, "user": user}

    @staticmethod
    def _path(directory: Path, record: dict[str, Any]) -> Path:
        """Name the file an answer to this prompt is stored in.

        Args:
            directory: The cache's directory.
            record: The provider's identity and both prompts.

        Returns:
            A file in the directory, named by a hash of the record.
        """
        digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()
        return directory / f"{digest}.json"

    def _read(self, system: str, user: str) -> str | None:
        """Read a stored answer to this prompt, if there is one.

        Args:
            system: The instructions.
            user: The question.

        Returns:
            The stored reply, or None when there is none, it can't be read, or what it was
            stored with doesn't match this prompt and provider exactly.
        """
        if self.directory is None:
            return None
        record = self._record(system, user)
        try:
            stored = json.loads(self._path(self.directory, record).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(stored, dict) or {k: stored.get(k) for k in record} != record:
            return None
        reply = stored.get("reply")
        return reply if isinstance(reply, str) else None

    def _write(self, system: str, user: str, reply: str) -> None:
        """Store an answer, replacing the file whole so a stopped run never leaves half of one.

        Args:
            system: The instructions.
            user: The question.
            reply: The model's reply.
        """
        if self.directory is None:
            return
        record = self._record(system, user)
        path = self._path(self.directory, record)
        self.directory.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        partial.write_text(json.dumps({**record, "reply": reply}, indent=1), encoding="utf-8")
        partial.replace(path)
