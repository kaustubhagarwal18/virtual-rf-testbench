# ADR 0027 — Persistent triage history in SQLite

## Context

The triage API keeps its history in memory (ADR 0010), so runs added with `POST /history`
are lost when it stops. That was acceptable on a laptop. It isn't in a container or a pod,
which restart as a matter of course. A restart also re-embeds the whole history through
Ollama, so start-up fails when Ollama is down, even though nothing about the history
changed.

## Decision

`--history-db` / `TRIAGE_HISTORY_DB` keeps the history in one SQLite file. Unset, nothing
changes. The search is still the `InMemoryRetriever` the evaluation uses: the file only
decides what is loaded into it.

- **Four modules, one job each:**
  - `history_db` owns the connection and durability: write-ahead log, `synchronous=FULL`, `BEGIN IMMEDIATE`, an integrity check at open.
  - `embed_cache` owns vectors and the model digest.
  - `history_store` owns the index, the write order and the swap.
  - `history_loader` owns the background thread and its backoff.
- **Write order.**
  - Embed the run through the cache, which commits the vector.
  - `INSERT OR IGNORE` the run, which commits it.
  - Only when that created a row, add it to the active index under the state lock.
  - So a 201 follows a commit, and a duplicate changes nothing. If the in-memory add fails after the commit, the index is marked stale: not ready, and `/triage` returns 503 until it's rebuilt from the file.
- **Vectors are cached under `(model, digest, sha256(text))`.**
  - The digest comes from Ollama's `/api/tags`, and the last verified one is stored.
  - With Ollama down, a fully cached history loads under the stored digest.
  - When Ollama answers again, the digest is checked. A changed one makes the service not ready until the history is re-embedded and swapped in, because query vectors from the new model mustn't be compared with the old index.
  - Vectors are committed one at a time, so an interrupted load resumes where it stopped.
- **The loader builds a complete index off to the side.** Under the state lock it then adds the runs committed since its snapshot and swaps the index in. Writers add under the same lock, so no committed run is missed and no request sees a partial index.
- **Readiness and liveness stay apart (ADR 0026).** Loading and rebuilding make `/readyz` answer 503 with the reason. `/livez` never depends on the history or Ollama.

## Alternatives considered

| Choice | Instead of | Why |
|---|---|---|
| SQLite on a volume | Postgres with pgvector | A few dozen runs need no server; pgvector stays the path once the history outgrows memory, behind the same interfaces. |
| SQLite | A JSON file | A JSON rewrite isn't atomic; SQLite gives transactions, a crash-safe log and an integrity check. |
| Cache vectors keyed by digest | Re-embed everything at start | Re-embedding is slow and needs Ollama to start at all; the cached vectors are byte-for-byte what Ollama returned. |
| Key by digest | Key by text alone | Pulling a new model under the same name would silently mix old and new vectors. |
| Build off to the side, catch up, swap | Load straight into the live index | A request could see a half-loaded index, and a run committed mid-load could be skipped. |

## Consequences

- Restarts keep added runs and don't re-embed. A cached history starts without Ollama.
- SQLite has one writer. The service must run as one replica per file (`replicas: 1`, `strategy: Recreate` in the chart).
- The file is user data: a corrupt one is reported, never deleted or replaced.
- The evaluation, retrieval maths, prompts, checks and behaviour lock are untouched. `make eval` never uses the API.
