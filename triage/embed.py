"""Turning text into a vector, behind one interface, so retrieval never depends on Ollama.

This is a different job from answering a prompt (triage/llm.py): an embedder only maps text
to a point in space, where similar text lands nearby. It has no prompt and no reply to parse,
so it gets its own small interface rather than being forced through LLMProvider.
"""

import hashlib
import json
import urllib.error
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from triage.llm import DEFAULT_HOST, ModelUnavailableError, open_url, unreachable_message

DEFAULT_EMBED_MODEL = "nomic-embed-text"
"""The embedding model pulled for this project; small, and runs locally."""


class Embedder(Protocol):
    """Anything that can turn texts into vectors."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text.

        Args:
            texts: The texts to embed.

        Returns:
            One vector per text, in order.
        """


@dataclass(frozen=True)
class OllamaEmbedder:
    """The embedding model, running locally under Ollama.

    Attributes:
        model: Which embedding model to use.
        host: Where Ollama listens.
        timeout_s: How long one embedding may take.
    """

    model: str = DEFAULT_EMBED_MODEL
    host: str = DEFAULT_HOST
    timeout_s: float = 60.0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text with one request per text.

        Args:
            texts: The texts to embed.

        Returns:
            One vector per text. A failed request propagates ModelUnavailableError from
            _embed_one unchanged.
        """
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        """Embed a single text.

        Args:
            text: The text.

        Returns:
            Its vector.

        Raises:
            ModelUnavailableError: If the request fails or the reply has no embedding.
        """
        body = {"model": self.model, "prompt": text}
        try:
            answer = json.loads(open_url(f"{self.host}/api/embeddings", self.timeout_s, body))
        except (urllib.error.URLError, TimeoutError) as error:
            raise ModelUnavailableError(unreachable_message(self.host, error)) from error
        embedding = answer.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise ModelUnavailableError(f"no embedding in the reply: {str(answer)[:200]}")
        return [float(value) for value in embedding]


FAKE_WIDTH = 64
"""The fixed width of a FakeEmbedder vector: words hash into this many buckets."""


@dataclass
class FakeEmbedder:
    """A deterministic, fixed-width embedder for tests: word counts hashed into buckets.

    Real embedders return a vector of fixed width whatever the text; the fake does too, by
    hashing each word into one of FAKE_WIDTH buckets. Texts that share words land near each
    other, which is all the retrieval tests need, and it runs offline. The width never
    depends on which texts were seen, so an index and a later query always match.

    Attributes:
        calls: Each batch of texts embedded, for a test to check.
    """

    calls: list[Sequence[str]] = field(default_factory=list)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text as hashed word counts in a fixed-width vector.

        Args:
            texts: The texts to embed.

        Returns:
            One FAKE_WIDTH-long vector per text.
        """
        self.calls.append(texts)
        vectors = []
        for text in texts:
            vector = [0.0] * FAKE_WIDTH
            for word in text.lower().split():
                vector[_bucket(word)] += 1.0
            vectors.append(vector)
        return vectors


def _bucket(word: str) -> int:
    """Map a word to one of FAKE_WIDTH buckets, the same way every time.

    Args:
        word: The word.

    Returns:
        Its bucket index.
    """
    return int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % FAKE_WIDTH
