"""A history that survives restarts: the in-memory index, rebuilt from SQLite.

The index the service searches is still an InMemoryRetriever, so retrieval is exactly what
it was. This wraps it with three guarantees:

- Durable writes. A run added over HTTP is embedded (its vector committed to the cache),
  then inserted (committed), and only then added to the index in memory. A 201 follows a
  commit, and a crash at any step leaves the database complete, so a restart restores it.
- Idempotent duplicates. The run's neutral id is unique in the database; a second
  submission inserts nothing and changes nothing in memory.
- No partial index, no lost run. A load builds a new index off to the side, then, under the
  state lock, adds the runs committed since its snapshot and swaps it in. A writer adds to
  the active index under the same lock, so every committed run reaches the index that ends
  up active. When an update in memory fails after a commit, the index is marked stale: the
  service reports not ready until it is rebuilt from the database, rather than serving a
  history that is missing a run.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from triage.bench_run import load_case
from triage.case import Case
from triage.embed import Embedder
from triage.embed_cache import CachedEmbedder, DigestSource, EmbeddingCache
from triage.health import StoreStatus
from triage.history_db import Database, DatabaseError
from triage.label import load_label
from triage.llm import ModelUnavailableError
from triage.pack import DEFAULT_PACK, Pack
from triage.prompt import Source
from triage.retrieval import DEFAULT_TOP_K, InMemoryRetriever, history_source, normalise

STALE_REASON = "history index out of date; rebuilding"
MODEL_CHANGED_REASON = "the embedding model's digest changed; rebuilding the history index"
LOADING_REASON = "history loading"

_LOG = logging.getLogger(__name__)


class HistoryUnavailableError(Exception):
    """The history can't serve a request now; the message says why."""


@dataclass(frozen=True)
class HistoryConfig:
    """What a persistent history is built from.

    Attributes:
        dataset: The folder of labelled history runs that ship with the service.
        inner: The embedder asked for vectors the cache lacks.
        model: The embedding model's name.
        digest_source: Reads the model's current digest from Ollama.
        pack: The domain pack that words the evidence.
        clock: The time now, in seconds since the epoch; injectable for tests.
    """

    dataset: Path
    inner: Embedder
    model: str
    digest_source: DigestSource
    pack: Pack = DEFAULT_PACK
    clock: Callable[[], float] = time.time


@dataclass
class _IndexState:
    """What the state lock guards.

    Attributes:
        index: The active index, or None before the first load completes.
        embedder: The cached embedder the active index uses.
        digest: The digest the active index was built with.
        digest_verified: Whether Ollama confirmed that digest since start-up.
        rebuild_reason: Why the active index can't be used until a rebuild, or None.
        waiting_reason: Why the last load attempt failed, while there is no index yet.
    """

    index: InMemoryRetriever | None = None
    embedder: CachedEmbedder | None = None
    digest: str | None = None
    digest_verified: bool = False
    rebuild_reason: str | None = None
    waiting_reason: str = "starting"


@dataclass
class PersistentHistory:
    """The service's searchable history, kept in SQLite and served from memory.

    Attributes:
        config: What it is built from.
        database: The open database, or None when it couldn't be opened.
        open_error: Why the database couldn't be opened, or None.
        state: The active index and its readiness, guarded by lock.
        lock: The state lock: writers' in-memory adds and the loader's swap take it.
        rebuild: Set when the loader should rebuild now, rather than wait.
    """

    config: HistoryConfig
    database: Database | None
    open_error: str | None = None
    state: _IndexState = field(default_factory=_IndexState)
    lock: threading.Lock = field(default_factory=threading.Lock)
    rebuild: threading.Event = field(default_factory=threading.Event)

    @classmethod
    def open(cls, path: Path, config: HistoryConfig) -> "PersistentHistory":
        """Open the database; a file that can't be used leaves the history not ready.

        Args:
            path: The database file.
            config: What the history is built from.

        Returns:
            The history, not yet loaded: the loader loads it.
        """
        try:
            return cls(config, Database.open(path))
        except DatabaseError as error:
            return cls(config, None, open_error=str(error))

    def not_ready_reason(self) -> str | None:
        """Say why the history can't serve yet.

        Returns:
            One line saying what it waits for, or None when it is ready.
        """
        with self.lock:
            return self._reason()

    def __len__(self) -> int:
        """Count the runs in the active index.

        Returns:
            The count; 0 before the first load completes.
        """
        with self.lock:
            return len(self.state.index) if self.state.index is not None else 0

    def similar_to(
        self, run: Case, count: int = DEFAULT_TOP_K, exclude_faults: frozenset[str] = frozenset()
    ) -> list[Source]:
        """Search the active index, never a partial or stale one.

        Args:
            run: The run being triaged.
            count: How many to return.
            exclude_faults: Faults whose runs are left out.

        Returns:
            The closest past runs.

        Raises:
            HistoryUnavailableError: If the history isn't ready; the message says why.
        """
        with self.lock:
            reason = self._reason()
            index = self.state.index
        if reason is not None or index is None:
            raise HistoryUnavailableError(reason or LOADING_REASON)
        return index.similar_to(run, count, exclude_faults)

    def add(self, run: Case, label: dict[str, Any]) -> Source:
        """Add a resolved run: embed it, commit it, then put it in the active index.

        Args:
            run: The run's evidence.
            label: Its fault, cause, fix and, optionally, action.

        Returns:
            The source it became; the same source for a run submitted twice.
        """
        database = self._open_database()
        source = history_source(run, label, self.config.pack)
        self._writer_embedder(database).embed_and_store([source.text])
        created = _insert_run(database, source, label, self.config.clock())
        with self.lock:
            self._apply(run, label, source, created)
        return source

    def load(self) -> None:
        """Build a complete index from the data set and the stored runs, then swap it in.

        The build runs without the state lock. The catch-up (runs committed since the
        snapshot) and the swap run under it, so no committed run is left out.
        """
        database = self._open_database()
        digest, verified = self._resolve_digest(database)
        embedder = CachedEmbedder(
            self.config.inner, EmbeddingCache(database), self.config.model, digest
        )
        snapshot = _max_seq(database)
        sources = _unique([*self._dataset_sources(), *_stored_sources(database, 0, snapshot)])
        built = _extended(None, sources, embedder, self.config.pack)
        with self.lock:
            known = {source.source_id for source in built.sources}
            later = [s for s in _stored_sources(database, snapshot) if s.source_id not in known]
            self.state = _IndexState(
                index=_extended(built, _unique(later), embedder, self.config.pack),
                embedder=embedder,
                digest=digest,
                digest_verified=verified,
            )
        EmbeddingCache(database).prune(self.config.model, digest)

    def verify_digest(self) -> bool:
        """Check the active index's digest against Ollama's.

        Returns:
            True when the digest changed and the index must be rebuilt; it is then not
            ready. False when it matched, or Ollama couldn't be asked.
        """
        try:
            current = self.config.digest_source()
        except ModelUnavailableError:
            return False
        with self.lock:
            if self.state.digest != current:
                self.state.rebuild_reason = MODEL_CHANGED_REASON
                return True
            self.state.digest_verified = True
        if self.database is not None:
            EmbeddingCache(self.database).record_digest(
                self.config.model, current, self.config.clock()
            )
        return False

    def wait_reason(self, reason: str) -> None:
        """Record why the last load attempt failed, for readiness to report.

        Args:
            reason: One line from the failure.
        """
        with self.lock:
            self.state.waiting_reason = reason

    def status(self) -> StoreStatus:
        """Describe the history for the dependency report.

        Returns:
            The digest in use, whether it is verified, and the stored and cached counts.
        """
        with self.lock:
            embedder, digest = self.state.embedder, self.state.digest
            verified = self.state.digest_verified
        stored = 0
        if self.database is not None:
            stored = int(self.database.query("SELECT COUNT(*) FROM runs")[0][0])
        return StoreStatus(
            digest=digest,
            digest_verified=verified,
            stored_runs=stored,
            cache_hits=embedder.hits if embedder is not None else 0,
            cache_misses=embedder.misses if embedder is not None else 0,
        )

    def _reason(self) -> str | None:
        """Say why the history can't serve, with the state lock held.

        Returns:
            The reason, or None when it is ready.
        """
        if self.open_error is not None:
            return self.open_error
        if self.state.rebuild_reason is not None:
            return self.state.rebuild_reason
        if self.state.index is None:
            return f"{LOADING_REASON}: {self.state.waiting_reason}"
        return None

    def _open_database(self) -> Database:
        """Return the database, or say why there is none.

        Returns:
            The open database.

        Raises:
            HistoryUnavailableError: If it couldn't be opened.
        """
        if self.database is None:
            raise HistoryUnavailableError(self.open_error or "no history database")
        return self.database

    def _writer_embedder(self, database: Database) -> CachedEmbedder:
        """Return the embedder a new run is stored with: the active index's, if loaded.

        Args:
            database: The open database.

        Returns:
            The cached embedder; before the first load, one under Ollama's current digest.
            ModelUnavailableError from the digest source propagates when Ollama is down.
        """
        with self.lock:
            embedder = self.state.embedder
        if embedder is not None:
            return embedder
        digest = self.config.digest_source()
        return CachedEmbedder(
            self.config.inner, EmbeddingCache(database), self.config.model, digest
        )

    def _apply(self, run: Case, label: dict[str, Any], source: Source, created: bool) -> None:
        """Put a committed run in the active index, with the state lock held.

        Args:
            run: The run's evidence.
            label: Its label.
            source: The source it became.
            created: Whether the insert created the row; False for a duplicate.
        """
        index = self.state.index
        if index is None:
            return
        known = any(existing.source_id == source.source_id for existing in index.sources)
        if not created:
            if not known:
                self._mark_stale(source.source_id, "a stored run is missing from the index")
            return
        try:
            index.add(run, label)
        except (ValueError, MemoryError, ModelUnavailableError) as error:
            self._mark_stale(source.source_id, str(error))

    def _mark_stale(self, source_id: str, cause: str) -> None:
        """Stop serving the active index until the loader rebuilds it from the database.

        Args:
            source_id: The run whose update failed.
            cause: What went wrong.
        """
        self.state.rebuild_reason = STALE_REASON
        self.rebuild.set()
        _LOG.error(
            json.dumps({"event": "history_index_stale", "source_id": source_id, "cause": cause})
        )

    def _resolve_digest(self, database: Database) -> tuple[str, bool]:
        """Find the digest to build under: Ollama's when it answers, else the stored one.

        Args:
            database: The open database.

        Returns:
            The digest, and whether Ollama confirmed it just now.

        Raises:
            ModelUnavailableError: If Ollama can't be asked and no digest was ever stored.
        """
        cache = EmbeddingCache(database)
        try:
            digest = self.config.digest_source()
        except ModelUnavailableError:
            stored = cache.stored_digest(self.config.model)
            if stored is None:
                raise
            return stored, False
        cache.record_digest(self.config.model, digest, self.config.clock())
        return digest, True

    def _dataset_sources(self) -> list[Source]:
        """Read the shipped history runs as sources, in folder order.

        Returns:
            One source per run folder, as InMemoryRetriever.from_history reads them.
        """
        folders = sorted(folder for folder in self.config.dataset.iterdir() if folder.is_dir())
        runs = [load_case(folder) for folder in folders]
        return [history_source(run, load_label(run), self.config.pack) for run in runs]


def _unique(sources: list[Source]) -> list[Source]:
    """Keep the first source with each id, in order, as the in-memory index does.

    Args:
        sources: The sources, possibly with repeated ids.

    Returns:
        The sources with each id once.
    """
    seen: dict[str, Source] = {}
    for source in sources:
        seen.setdefault(source.source_id, source)
    return list(seen.values())


def _extended(
    base: InMemoryRetriever | None, sources: list[Source], embedder: CachedEmbedder, pack: Pack
) -> InMemoryRetriever:
    """Build an index of a base index's sources plus new ones, embedding through the cache.

    Args:
        base: The index to extend, or None to start empty.
        sources: The sources to add, none of them already in base.
        embedder: Embeds them, storing the vectors the cache lacks.
        pack: The domain pack the index words queries with.

    Returns:
        A new index; base is left as it was.
    """
    old_sources = list(base.sources) if base is not None else []
    old_vectors = base.vectors if base is not None else np.zeros((0, 0))
    if not sources:
        return InMemoryRetriever(old_sources, old_vectors, embedder, pack)
    new_vectors = normalise(np.array(embedder.embed_and_store([s.text for s in sources])))
    vectors = np.vstack([old_vectors, new_vectors]) if old_sources else new_vectors
    return InMemoryRetriever([*old_sources, *sources], vectors, embedder, pack)


def _insert_run(database: Database, source: Source, label: dict[str, Any], now: float) -> bool:
    """Commit a run, unless one with the same id is already stored.

    Args:
        database: The open database.
        source: The source the run became.
        label: Its label, kept whole.
        now: When it was added.

    Returns:
        True when the row was created; False for a duplicate.
    """
    with database.transaction() as cursor:
        cursor.execute(
            "INSERT OR IGNORE INTO runs (source_id, text, fault, action, fix_applied, "
            "symptom_text, label, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source.source_id,
                source.text,
                source.fault,
                source.action,
                source.fix_applied,
                source.symptom_text,
                json.dumps(label, sort_keys=True),
                now,
            ),
        )
        return int(cursor.rowcount) == 1


def _max_seq(database: Database) -> int:
    """Read the highest sequence number committed, the loader's snapshot.

    Args:
        database: The open database.

    Returns:
        The highest seq, or 0 when no run is stored.
    """
    return int(database.query("SELECT COALESCE(MAX(seq), 0) FROM runs")[0][0])


def _stored_sources(database: Database, after: int, up_to: int | None = None) -> list[Source]:
    """Read the stored runs with a sequence number in a range, in commit order.

    Args:
        database: The open database.
        after: Rows with seq above this.
        up_to: Rows with seq at most this; None for no upper bound.

    Returns:
        The rows as sources.
    """
    rows = database.query(
        "SELECT source_id, text, fault, action, fix_applied, symptom_text FROM runs "
        "WHERE seq > ? AND seq <= ? ORDER BY seq",
        (after, up_to if up_to is not None else 2**63 - 1),
    )
    return [
        Source(
            source_id=row[0],
            text=row[1],
            fault=row[2],
            action=row[3],
            fix_applied=row[4],
            symptom_text=row[5],
        )
        for row in rows
    ]
