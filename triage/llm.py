"""The answering model behind one small interface, so the pipeline never depends on Ollama.

The unit tests pass a fake, which is what keeps them offline, fast and repeatable, and lets
them produce replies a real model would rarely give: truncated JSON, an invented citation,
an action that isn't on the list. Only the evaluation and one marked integration test talk
to Ollama.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

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


def _open(request: "urllib.request.Request | str", timeout_s: float) -> bytes:
    """Open an http(s) URL and return its body, refusing any other scheme.

    Args:
        request: The request or URL to open.
        timeout_s: How long it may take.

    Returns:
        The response body.

    Raises:
        ModelUnavailableError: If the URL isn't http(s); a file: or custom scheme would be a
            misconfiguration, not a model to reach.
    """
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    if urllib.parse.urlparse(url).scheme not in ALLOWED_SCHEMES:
        raise ModelUnavailableError(f"refusing to open a non-http(s) URL: {url}")
    # The scheme is checked on the line above; bandit can't see that, so B310 is silenced here.
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # nosec B310
        body: bytes = response.read()
    return body


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
    """

    model: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    temperature: float = 0.0
    seed: int = 42
    timeout_s: float = DEFAULT_TIMEOUT_S

    def complete(self, system: str, user: str) -> str:
        """Ask the model, requiring JSON back.

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
            "format": "json",
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "seed": self.seed,
                "num_ctx": CONTEXT_TOKENS,
            },
        }
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            json.dumps(body).encode(),
            {"Content-Type": "application/json"},
        )
        try:
            answer = json.loads(_open(request, self.timeout_s))
        except urllib.error.HTTPError as error:
            raise ModelUnavailableError(
                f"{self.host} refused the request ({error.code}); is {self.model} pulled?"
            ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ModelUnavailableError(
                f"can't reach Ollama at {self.host} ({error}); start it with 'ollama serve'"
            ) from error
        except json.JSONDecodeError as error:
            raise ModelUnavailableError(f"{self.host} didn't answer with JSON: {error}") from error
        content = answer.get("message", {}).get("content")
        if not isinstance(content, str):
            raise ModelUnavailableError(f"no reply content in {str(answer)[:200]}")
        return content

    def available(self) -> bool:
        """Check whether Ollama is running, so a test can skip instead of failing.

        Returns:
            True if it answers.
        """
        try:
            _open(f"{self.host}/api/version", timeout_s=2.0)
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
