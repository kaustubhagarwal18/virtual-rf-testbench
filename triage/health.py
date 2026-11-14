"""Whether the triage service is alive, ready, and whether what it depends on is up.

Three questions, kept apart on purpose:

- Alive: the process answers. A liveness probe asks only this, so a restart fixes only what a
  restart can fix.
- Ready: it can serve a request now, with its history loaded. A readiness probe asks this,
  so traffic waits for the history instead of getting answers without it.
- Dependencies: is Ollama reachable and are its models there. Nothing probes this. An Ollama
  outage shows here and in each affected request's 503, but it never makes a probe fail, so
  Kubernetes doesn't restart a healthy process because something it calls is down.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from triage.llm import ModelUnavailableError, open_url

DEPENDENCY_TIMEOUT_S = 2.0
"""How long the dependency check waits for Ollama: short, because a person is waiting."""

DEFAULT_TAG = "latest"
"""The tag Ollama gives a model pulled without one: `nomic-embed-text` is listed as
`nomic-embed-text:latest`."""

Opener = Callable[[str, float], bytes]
"""Fetches a URL's body with a timeout; triage.llm.open_url, or a fake in tests."""


class Readiness(Protocol):
    """Anything that can say whether the service can serve requests now."""

    def not_ready_reason(self) -> str | None:
        """Say why the service can't serve yet.

        Returns:
            One line saying what it is waiting for, or None when it is ready.
        """


@dataclass(frozen=True)
class AlwaysReady:
    """The readiness of a service whose history was loaded before it started serving."""

    def not_ready_reason(self) -> str | None:
        """Report ready: everything was loaded before the first request.

        Returns:
            None, always.
        """
        return None


ALWAYS_READY = AlwaysReady()
"""The one AlwaysReady the service needs; it has no state."""


@dataclass(frozen=True)
class ModelStatus:
    """Whether one model is pulled on the Ollama the service uses.

    Attributes:
        name: The model as configured, such as qwen2.5:3b.
        present: Whether Ollama lists it.
        digest: Its digest as Ollama lists it, or None when it isn't listed.
    """

    name: str
    present: bool
    digest: str | None


@dataclass(frozen=True)
class OllamaStatus:
    """What the service found when it asked Ollama.

    Attributes:
        host: Where it asked.
        reachable: Whether Ollama answered both requests with usable JSON.
        version: Ollama's version, or None when it didn't answer.
        error: One line saying what went wrong, or None when it answered.
        models: Each configured model's status, in the order configured.
    """

    host: str
    reachable: bool
    version: str | None
    error: str | None
    models: tuple[ModelStatus, ...]

    @property
    def healthy(self) -> bool:
        """Whether Ollama answered and every configured model is pulled.

        Returns:
            True only when nothing the service needs from Ollama is missing.
        """
        return self.reachable and all(model.present for model in self.models)


def full_model_name(name: str) -> str:
    """Spell a model the way Ollama lists it, adding the default tag when there is none.

    Args:
        name: A model name, with or without a tag.

    Returns:
        The name with its tag, such as nomic-embed-text:latest.
    """
    return name if ":" in name else f"{name}:{DEFAULT_TAG}"


def _json_object(body: bytes) -> dict[str, Any]:
    """Parse a reply that must be a JSON object.

    Args:
        body: The reply's body.

    Returns:
        The object.

    Raises:
        ValueError: If the body isn't JSON, or is JSON but not an object.
    """
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def ollama_status(
    host: str,
    models: Sequence[str],
    timeout_s: float = DEPENDENCY_TIMEOUT_S,
    opener: Opener = open_url,
) -> OllamaStatus:
    """Ask Ollama whether it is up and which of the configured models it has.

    Uses `/api/version` and `/api/tags`; the latter lists each pulled model's `name` and
    `digest`. Any failure is reported in the result, never raised, because this answers a
    question about health rather than doing the service's work.

    Args:
        host: Where Ollama listens, such as http://127.0.0.1:11434.
        models: The models the service is configured to use.
        timeout_s: How long each request may take.
        opener: How a URL is fetched; triage.llm.open_url unless a test passes a fake.

    Returns:
        What was found: reachable or not, and each model's status.
    """
    try:
        version = _json_object(opener(f"{host}/api/version", timeout_s)).get("version")
        listed = _json_object(opener(f"{host}/api/tags", timeout_s)).get("models", [])
    # URLError and TimeoutError are OSErrors; ValueError covers a reply that isn't JSON.
    except (OSError, ValueError, ModelUnavailableError) as error:
        missing = tuple(ModelStatus(name, present=False, digest=None) for name in models)
        return OllamaStatus(host, reachable=False, version=None, error=str(error), models=missing)
    digests = {
        str(entry.get("name")): entry.get("digest") for entry in listed if isinstance(entry, dict)
    }
    statuses = []
    for name in models:
        digest = digests.get(full_model_name(name))
        present = full_model_name(name) in digests
        statuses.append(ModelStatus(name, present, str(digest) if digest is not None else None))
    return OllamaStatus(
        host,
        reachable=True,
        version=str(version) if version is not None else None,
        error=None,
        models=tuple(statuses),
    )


@dataclass(frozen=True)
class ServiceHealth:
    """How the service answers its readiness and dependency questions.

    Attributes:
        readiness: Says whether it can serve now; AlwaysReady when the history was loaded
            before serving started.
        dependencies: Asks Ollama for its status; None when nothing is configured to ask,
            as with a fake model in tests.
    """

    readiness: Readiness = ALWAYS_READY
    dependencies: Callable[[], OllamaStatus] | None = None
