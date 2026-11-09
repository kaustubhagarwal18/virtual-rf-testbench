# ADR 0010: The triage service over HTTP, with an in-memory history

- **Status:** Accepted
- **Date:** 2026-11-09

## Context

Triage ran only from the command line, on a run saved to disk. A bench, a CI job or a person
with a failed run needs to ask it over the network, and a resolved failure needs a way into
the history, so the next similar failure has it as a precedent. The planned service also
names Redis, an ingestion worker and Postgres with pgvector.

## Decisions

- **FastAPI, as an optional extra.** `fastapi` and `uvicorn` live under the `api` extra
  (`uv run --extra api`, which `make triage-api` uses), so the simulators' image and the core
  pipeline don't carry them. The app is built by `create_app(provider, history, checks)`, so
  the tests pass a fake model and a fake embedder and never touch the network.
- **Three endpoints.** `POST /triage` takes a run's evidence (the results file, and
  optionally the instrument logs and the sequence log) and returns the verdict.
  `POST /history` takes a resolved run with its label (fault, cause, fix) and returns the
  neutral id it will be cited by. `GET /health` reports whether a history is loaded and its
  size.
- **The same pipeline and checks as everywhere else.** The service calls `triage_run`, with
  the firmware check on. A request is untrusted data: it is validated (the results file must
  list tests with an id and an outcome; logs and sequence are capped at 2,000 lines) and goes
  into the prompt only as evidence. The model has no tools.
- **Errors are one line with a status.** An unreachable model is 503 and an unusable answer
  502, each with the hint that fixes it. `POST /history` on a service started with
  `--no-history` is 409.
- **The history stays in memory, and pgvector is deferred.** At start the service embeds
  `triage/data/runs/history/` (27 runs) into the same `InMemoryRetriever` the evaluation
  uses. At this size an exact in-memory search returns the same top 3 a database would. The
  `Retriever` and `HistoryStore` interfaces are what a pgvector store would implement, so it
  can drop in later without the endpoints noticing.
- **Localhost by default.** It binds `127.0.0.1` and talks only to the local Ollama, so no
  run data leaves the machine.

## Consequences

- A failed run can be triaged by anything that can POST JSON, and a resolved one can be
  added without rebuilding the data set.
- Runs added with `POST /history` are lost when the service stops. That is acceptable while
  the history is the data set in the repository; it is the reason to add a database later.
- Redis and the ingestion worker are not built: one synchronous request is fast enough for
  one bench, and a queue earns its place only when many benches post at once.
