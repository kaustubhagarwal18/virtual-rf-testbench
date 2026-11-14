# ADR 0026: Separate liveness, readiness and dependency health, and `TRIAGE_` Ollama settings

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The triage API is about to run in a container, and later in Kubernetes. Today it has one
`GET /health`, and it always talks to Ollama at `127.0.0.1:11434`. In a cluster Ollama runs
somewhere else (on the Mac, reached from Minikube), and the orchestrator asks two different
questions: should this process be restarted, and should it get traffic. An Ollama outage
should answer neither with "yes, restart it". Restarting the API can't bring Ollama back,
and a restart loop would hide the real fault.

## Decisions

- **Three endpoints for three questions** (`triage/health.py`):
  - `GET /livez`: the process answers. It checks nothing else.
  - `GET /readyz`: it can serve now. It asks an injected `Readiness`, so the history loader planned next can report "not ready" while it loads. Today the history is loaded before serving starts, so the service is always ready.
  - `GET /health/dependencies`: asks Ollama's `/api/version` and `/api/tags` with a 2 s timeout. It reports reachability, which configured models are pulled and their digests, and an overall `ok` or `degraded`. It always returns 200, because it is a report, not a verdict. Probes never use it.
- **Settings from the environment, with `TRIAGE_` prefixes.** `--ollama-host`, `--model` and `--embed-model` default to `TRIAGE_OLLAMA_HOST`, `TRIAGE_MODEL` and `TRIAGE_EMBED_MODEL`, then to today's values. A flag beats its variable, and an empty variable counts as unset.
- **A host that isn't an http(s) URL with a host name stops start-up** with one line, before the history is embedded.

## Alternatives considered

- **Point the probes at the existing `/health`.** Rejected. One endpoint can't say both "restart me" and "don't send me traffic", and adding an Ollama check to it would turn every Ollama outage into a restart loop. `/health` keeps its response unchanged for existing callers.
- **Make readiness depend on Ollama.** Rejected. When Ollama drops, every replica would leave the load balancer, though each could still say why a request fails. A per-request 503 with a reason is more useful.
- **Reuse `OLLAMA_HOST`.** Rejected. That variable is the Ollama server's own setting for where it listens (`0.0.0.0:11434`, with no scheme). On a Mac running both, one variable can't mean the server's bind address and the client's URL at once. The service ignores `OLLAMA_HOST`, and a test pins that.
- **Ollama's `/api/show` for the digest.** Not used. `/api/tags` lists each model's name and digest, as seen from a real Ollama. `/api/show` wasn't checked.

## Consequences

- The defaults, and the `/health`, `/triage` and `/history` responses, are unchanged, so existing callers and the evaluation see no difference. The evaluation doesn't use the API.
- Unchanged for now: start-up still exits 1 if Ollama can't embed the history. A background loader that keeps the process up and reports through `/readyz` is the next change.
- The Kubernetes chart will use `/livez` for liveness and `/readyz` for readiness.
