"""Embedding vectors kept in SQLite, so a restart doesn't re-embed the whole history.

A vector is stored under the embedding model's name, the model's digest as Ollama's
`/api/tags` lists it, and the SHA-256 of the text. The vector is the one Ollama returned, so
an index built from the cache is the index built by asking Ollama again: retrieval can't tell
the difference. A changed digest means a different model under the same name, so its vectors
are kept apart and the old ones pruned once nothing uses them.

The digest last verified against Ollama is stored too. When Ollama is down at start-up, the
cache is read under that digest, so a fully cached history loads without asking Ollama at all.
"""

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from triage.embed import Embedder
from triage.health import Opener, full_model_name, ollama_status
from triage.history_db import Database, DatabaseError
from triage.llm import ModelUnavailableError, open_url


def text_key(text: str) -> str:
    """Name a text by its SHA-256, so the cache never stores the text twice.

    Args:
        text: The text that was embedded.

    Returns:
        Its hex digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmbeddingCache:
    """The vectors and the verified digest, in the history database.

    Attributes:
        database: Where they are kept.
    """

    database: Database

    def stored_digest(self, model: str) -> str | None:
        """Read the digest last verified for a model.

        Args:
            model: The embedding model.

        Returns:
            Its digest, or None when it has never been verified.
        """
        rows = self.database.query("SELECT digest FROM embed_model WHERE model = ?", (model,))
        return str(rows[0][0]) if rows else None

    def record_digest(self, model: str, digest: str, verified_at: float) -> None:
        """Record the digest Ollama just reported for a model.

        Args:
            model: The embedding model.
            digest: Its digest.
            verified_at: When it was checked, in seconds since the epoch.
        """
        with self.database.transaction() as cursor:
            cursor.execute(
                "INSERT INTO embed_model (model, digest, verified_at) VALUES (?, ?, ?) "
                "ON CONFLICT (model) DO UPDATE SET digest = excluded.digest, "
                "verified_at = excluded.verified_at",
                (model, digest, verified_at),
            )

    def vectors(self, model: str, digest: str, keys: Sequence[str]) -> dict[str, list[float]]:
        """Read the cached vectors among the given text keys.

        Args:
            model: The embedding model.
            digest: Its digest.
            keys: The texts' keys, from text_key.

        Returns:
            Each cached key's vector; keys with no vector are left out. A stored vector that
            can't be read back propagates DatabaseError from _parse.
        """
        found: dict[str, list[float]] = {}
        for key in set(keys):
            rows = self.database.query(
                "SELECT vector FROM vectors WHERE model = ? AND digest = ? AND text_sha256 = ?",
                (model, digest, key),
            )
            if rows:
                found[key] = self._parse(rows[0][0])
        return found

    def _parse(self, stored: str) -> list[float]:
        """Read a stored vector back as a non-empty list of numbers.

        Args:
            stored: The vector as it was stored, JSON text.

        Returns:
            The vector.

        Raises:
            DatabaseError: If it isn't a non-empty JSON list of numbers; the message names
                the file and the fix.
        """
        try:
            parsed = json.loads(stored)
            values = [float(value) for value in parsed] if isinstance(parsed, list) else []
        except (TypeError, ValueError) as error:
            raise DatabaseError(self._unreadable(str(error))) from error
        if not values:
            raise DatabaseError(self._unreadable(f"expected a non-empty list: {stored[:40]!r}"))
        return values

    def _unreadable(self, problem: str) -> str:
        """Say that a stored vector can't be used, and how to recover.

        Args:
            problem: What is wrong with it.

        Returns:
            One line naming the file and the fix.
        """
        return (
            f"{self.database.path} holds an unreadable vector ({problem}); move the file "
            "aside and restart to rebuild the cache"
        )

    def store(self, model: str, digest: str, vectors: dict[str, list[float]]) -> None:
        """Store vectors, keeping any already there.

        Args:
            model: The embedding model.
            digest: Its digest.
            vectors: Each text key's vector.
        """
        with self.database.transaction() as cursor:
            cursor.executemany(
                "INSERT OR IGNORE INTO vectors (model, digest, text_sha256, vector) "
                "VALUES (?, ?, ?, ?)",
                [(model, digest, key, json.dumps(vector)) for key, vector in vectors.items()],
            )

    def prune(self, model: str, keep_digest: str) -> int:
        """Delete a model's vectors stored under any digest but the one in use.

        Args:
            model: The embedding model.
            keep_digest: The digest the active index was built with.

        Returns:
            How many vectors were deleted.
        """
        with self.database.transaction() as cursor:
            cursor.execute(
                "DELETE FROM vectors WHERE model = ? AND digest != ?", (model, keep_digest)
            )
            return int(cursor.rowcount)


@dataclass
class CachedEmbedder:
    """An embedder that reads vectors from the cache before asking the one behind it.

    Reading never writes: a question being triaged is embedded but not kept, so the cache
    holds only the history. embed_and_store is the path that keeps what it embeds.

    Attributes:
        inner: The embedder asked for vectors the cache doesn't have.
        cache: Where vectors are kept.
        model: The embedding model's name.
        digest: The digest the vectors are read and stored under.
        hits: How many texts were answered from the cache.
        misses: How many had to be embedded.
    """

    inner: Embedder
    cache: EmbeddingCache
    model: str
    digest: str
    hits: int = 0
    misses: int = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text, from the cache where it can, without storing anything.

        Args:
            texts: The texts.

        Returns:
            One vector per text, in order. A failure to reach the embedder propagates
            ModelUnavailableError from it unchanged.
        """
        return self._embed(texts, store=False)

    def embed_and_store(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text, from the cache where it can, and store the vectors it lacked.

        Args:
            texts: The texts.

        Returns:
            One vector per text, in order. The new vectors are committed before this
            returns.
        """
        return self._embed(texts, store=True)

    def _embed(self, texts: Sequence[str], store: bool) -> list[list[float]]:
        """Answer from the cache, ask the inner embedder for the rest, maybe store them.

        Args:
            texts: The texts.
            store: Whether to keep the vectors the cache lacked.

        Returns:
            One vector per text, in order.
        """
        keys = [text_key(text) for text in texts]
        found = self.cache.vectors(self.model, self.digest, keys)
        missing = {key: text for key, text in zip(keys, texts, strict=True) if key not in found}
        if missing:
            found.update(self._fetch(missing, store))
        missed = sum(1 for key in keys if key in missing)
        self.hits += len(keys) - missed
        self.misses += missed
        return [found[key] for key in keys]

    def _fetch(self, missing: dict[str, str], store: bool) -> dict[str, list[float]]:
        """Ask the inner embedder for the texts the cache lacks.

        When storing, each vector is committed as soon as it arrives, so a load interrupted
        part-way (Ollama going down, the process stopping) keeps what it had embedded and
        the next attempt resumes from there.

        Args:
            missing: Each missing text, by its key.
            store: Whether to keep the vectors.

        Returns:
            Each key's vector. A failure to reach the embedder propagates
            ModelUnavailableError from it unchanged, after the vectors before it are stored.
        """
        if not store:
            return dict(zip(missing, self._ask(list(missing.values())), strict=True))
        fetched: dict[str, list[float]] = {}
        for key, text in missing.items():
            vector = self._ask([text])[0]
            self.cache.store(self.model, self.digest, {key: vector})
            fetched[key] = vector
        return fetched

    def _ask(self, texts: list[str]) -> list[list[float]]:
        """Ask the inner embedder, treating an unusable reply as an unavailable model.

        Ollama can answer with something that isn't JSON, or with an embedding that holds
        something other than numbers. Either way there is no vector to use, which callers
        already handle as ModelUnavailableError: a 503, or a load retried with backoff.

        Args:
            texts: The texts to embed.

        Returns:
            One vector of floats per text. ModelUnavailableError from the inner embedder
            propagates unchanged.

        Raises:
            ModelUnavailableError: If the reply can't be read as one list of numbers per text.
        """
        try:
            usable = [[float(value) for value in vector] for vector in self.inner.embed(texts)]
        except (TypeError, ValueError) as error:
            raise ModelUnavailableError(
                f"the embedding model's reply couldn't be used ({error}); check that "
                f"{self.model} is an embedding model and Ollama is healthy"
            ) from error
        if len(usable) != len(texts):
            raise ModelUnavailableError(
                f"the embedding model returned {len(usable)} vectors for {len(texts)} texts"
            )
        return usable


DigestSource = Callable[[], str]
"""Returns the embedding model's current digest, raising ModelUnavailableError when Ollama
can't say."""


def ollama_digest(host: str, model: str, opener: Opener = open_url) -> str:
    """Ask Ollama for a model's digest, as `/api/tags` lists it.

    Args:
        host: Where Ollama listens.
        model: The embedding model.
        opener: How a URL is fetched; triage.llm.open_url unless a test passes a fake.

    Returns:
        The model's digest.

    Raises:
        ModelUnavailableError: If Ollama can't be reached, or doesn't list the model, or
            lists it without a digest; the message says what to do.
    """
    found = ollama_status(host, [model], opener=opener)
    if not found.reachable:
        raise ModelUnavailableError(f"can't read {model}'s digest from {host}: {found.error}")
    status = found.models[0]
    if not status.present:
        raise ModelUnavailableError(
            f"{full_model_name(model)} isn't pulled on {host}; run 'ollama pull {model}'"
        )
    if status.digest is None:
        raise ModelUnavailableError(f"{host} lists {full_model_name(model)} without a digest")
    return status.digest
