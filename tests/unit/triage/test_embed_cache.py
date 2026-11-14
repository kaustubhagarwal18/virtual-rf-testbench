"""Tests for the embedding cache: hits, misses, digests, and resuming an interrupted load."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.unit.triage.history_fakes import DIGEST, NEW_DIGEST, NOW, CountingEmbedder
from triage.embed import FakeEmbedder
from triage.embed_cache import CachedEmbedder, EmbeddingCache, ollama_digest, text_key
from triage.health import Opener
from triage.history_db import Database, DatabaseError
from triage.llm import ModelUnavailableError

MODEL = "nomic-embed-text"
TEXTS = ["first text", "second text", "third text"]


def cached(tmp_path: Path, inner: CountingEmbedder, digest: str = DIGEST) -> CachedEmbedder:
    """Return a cached embedder over a database in tmp_path, under the given digest."""
    cache = EmbeddingCache(Database.open(tmp_path / "history.db"))
    return CachedEmbedder(inner, cache, MODEL, digest)


def test_a_stored_vector_is_read_back_without_asking_the_embedder(tmp_path: Path) -> None:
    """The second embedding of the same texts is all hits, and the vectors are identical."""
    inner = CountingEmbedder()
    embedder = cached(tmp_path, inner)
    first = embedder.embed_and_store(TEXTS)
    asked = len(inner.texts)
    second = embedder.embed_and_store(TEXTS)
    assert second == first == FakeEmbedder().embed(TEXTS)
    assert len(inner.texts) == asked == len(TEXTS)
    assert (embedder.hits, embedder.misses) == (len(TEXTS), len(TEXTS))


def test_embedding_a_question_never_stores_it(tmp_path: Path) -> None:
    """embed() reads the cache but keeps nothing, so questions don't fill it."""
    inner = CountingEmbedder()
    embedder = cached(tmp_path, inner)
    embedder.embed(["a question being triaged"])
    embedder.embed(["a question being triaged"])
    asked_twice = 2
    assert len(inner.texts) == asked_twice


def test_a_changed_digest_misses_the_old_vectors(tmp_path: Path) -> None:
    """Vectors stored under one digest are not offered under another."""
    inner = CountingEmbedder()
    cached(tmp_path, inner, DIGEST).embed_and_store(TEXTS)
    newer = cached(tmp_path, inner, NEW_DIGEST)
    newer.embed_and_store(TEXTS)
    assert newer.misses == len(TEXTS)
    assert len(inner.texts) == 2 * len(TEXTS)


@pytest.mark.parametrize("embedded_before_failure", [0, 1, 2])
def test_an_interrupted_store_keeps_what_it_embedded(
    tmp_path: Path, embedded_before_failure: int
) -> None:
    """Vectors embedded before a failure are committed, so a retry asks only for the rest."""
    failing = CountingEmbedder(fail_after=embedded_before_failure)
    with pytest.raises(ModelUnavailableError):
        cached(tmp_path, failing).embed_and_store(TEXTS)
    retry = CountingEmbedder()
    cached(tmp_path, retry).embed_and_store(TEXTS)
    assert retry.texts == TEXTS[embedded_before_failure:]


def test_the_verified_digest_is_stored_and_replaced(tmp_path: Path) -> None:
    """The last digest recorded for a model is the one read back."""
    cache = EmbeddingCache(Database.open(tmp_path / "history.db"))
    assert cache.stored_digest(MODEL) is None
    cache.record_digest(MODEL, DIGEST, NOW)
    cache.record_digest(MODEL, NEW_DIGEST, NOW)
    assert cache.stored_digest(MODEL) == NEW_DIGEST


def test_pruning_keeps_only_the_digest_in_use(tmp_path: Path) -> None:
    """Old-digest vectors go; the current digest's stay."""
    inner = CountingEmbedder()
    cached(tmp_path, inner, DIGEST).embed_and_store(TEXTS)
    current = cached(tmp_path, inner, NEW_DIGEST)
    current.embed_and_store(TEXTS)
    assert current.cache.prune(MODEL, NEW_DIGEST) == len(TEXTS)
    keys = [text_key(text) for text in TEXTS]
    assert not current.cache.vectors(MODEL, DIGEST, keys)
    assert len(current.cache.vectors(MODEL, NEW_DIGEST, keys)) == len(TEXTS)


TAGS = {"models": [{"name": "nomic-embed-text:latest", "digest": DIGEST}]}


def tags_opener(tags: object) -> Opener:
    """Return an opener answering /api/version and /api/tags with the given tags."""

    def opener(url: str, timeout_s: float) -> bytes:
        """Answer one request."""
        del timeout_s
        reply = {"version": "0.40.14"} if url.endswith("/api/version") else tags
        return json.dumps(reply).encode()

    return opener


def test_the_digest_is_read_from_api_tags() -> None:
    """A model named without a tag matches its :latest entry, and its digest comes back."""
    assert ollama_digest("http://ollama", MODEL, opener=tags_opener(TAGS)) == DIGEST


@pytest.mark.parametrize(
    ("tags", "message"),
    [
        ({"models": []}, "ollama pull"),
        ({"models": [{"name": "nomic-embed-text:latest"}]}, "without a digest"),
        ({"models": None}, "can't read"),
    ],
    ids=["not-pulled", "no-digest", "bad-reply"],
)
def test_a_digest_that_cant_be_read_says_why(tags: object, message: str) -> None:
    """A missing model, a missing digest or a bad reply is ModelUnavailableError with the fix."""
    with pytest.raises(ModelUnavailableError, match=message):
        ollama_digest("http://ollama", MODEL, opener=tags_opener(tags))


class OddEmbedder:
    """An embedder whose reply is unusable in a given way, as a misbehaving Ollama's can be."""

    def __init__(self, reply: Any) -> None:
        """Answer every call with this reply, or raise it when it is an exception."""
        self.reply = reply

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return or raise the configured reply."""
        del texts
        if isinstance(self.reply, Exception):
            raise self.reply
        reply: list[list[float]] = self.reply
        return reply


@pytest.mark.parametrize(
    "reply",
    [
        json.JSONDecodeError("Expecting value", "<html>", 0),
        ValueError("could not convert string to float: 'x'"),
        TypeError("float() argument must be a string or a real number"),
        [["x", "y"]],
        [[None, 1.0]],
        [[1.0], [2.0]],
        [],
    ],
    ids=["not-json", "value-error", "type-error", "strings", "null", "too-many", "none"],
)
@pytest.mark.parametrize("store", [False, True], ids=["embed", "embed_and_store"])
def test_an_unusable_embedding_reply_is_an_unavailable_model(
    tmp_path: Path, reply: object, store: bool
) -> None:
    """Every malformed reply becomes ModelUnavailableError, which callers already handle."""
    cache = EmbeddingCache(Database.open(tmp_path / "history.db"))
    embedder = CachedEmbedder(OddEmbedder(reply), cache, MODEL, DIGEST)
    with pytest.raises(ModelUnavailableError, match="embedding model"):
        if store:
            embedder.embed_and_store(["one text"])
        else:
            embedder.embed(["one text"])
    assert not cache.vectors(MODEL, DIGEST, [text_key("one text")])


@pytest.mark.parametrize("stored", ["not json", '["x"]', "{}", "3"])
def test_an_unreadable_stored_vector_is_a_database_error(tmp_path: Path, stored: str) -> None:
    """A vector row that can't be read back as numbers names the file and the fix."""
    database = Database.open(tmp_path / "history.db")
    with database.transaction() as cursor:
        cursor.execute(
            "INSERT INTO vectors VALUES (?, ?, ?, ?)", (MODEL, DIGEST, text_key("t"), stored)
        )
    with pytest.raises(DatabaseError, match="move the file aside"):
        EmbeddingCache(database).vectors(MODEL, DIGEST, [text_key("t")])
