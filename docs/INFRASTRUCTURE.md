# Infrastructure

This document covers the container images, how history is stored, running locally with
Compose, and the CI chain.

## Images

### Simulators image

Built by `make image` from the `runtime` stage (the Dockerfile's last stage and default
target). Contains the signal generator and spectrum analyzer, run by choosing a CMD.

### Triage image

Built by `make triage-image` from the `triage-runtime` stage. Contains:

- The triage API (`triage/`), its seed history (39 runs at `triage/data/runs/history/`) and
  its packs.
- Runtime dependencies: fastapi, uvicorn, pydantic, numpy and the stdlib.
- The same base image and hardening as the simulators image: apt upgrade applied at build
  time, pip removed, runs as uid 10001.

Absent from the triage image: pip, rsinstrument, the simulators, the drivers, the answer-key
questions (`triage/data/runs/questions/`) and the evaluation results (`triage/results/`).

Both images share one Dockerfile. The triage stages copy nothing the simulator stages copy,
so the simulator image is unaffected by triage changes (ADR 0028).

**Sizes:** triage 233 MB, simulators 216 MB.

## Where history lives

History has two parts that are stored separately:

| Part | What it contains | Where |
|---|---|---|
| Seed history | 39 past runs shipped with the code, ~1.6 MB | Baked into the triage image at `triage/data/runs/history/` |
| Added history | Runs posted to `POST /history`; embedding vectors for every run | SQLite at `/data/history.sqlite` on the named volume |

At start-up the loader reads both: it indexes the seed runs from the image files and the
added runs from the database. Vectors for every run are cached in the database, so a warm
start does not call Ollama.

Deleting the named volume loses the added runs and the embedding cache. The next start
re-embeds the seed runs from scratch, which needs Ollama.

The database path is controlled by `--history-db` / `TRIAGE_HISTORY_DB`. Unset, the API
runs without persistence.

## Running locally with Compose

```
make bench-up          # starts the simulators only (no profile flag)
make bench-down

make triage-up         # starts the triage API (needs Docker Desktop)
make triage-down
```

`make triage-up` runs `docker compose --profile triage up`. The triage service:

- calls `http://host.docker.internal:11434` for Ollama by default (`TRIAGE_OLLAMA_HOST`);
- keeps history in the named volume `triage-history` at `/data`;
- runs read-only with a tmpfs at `/tmp`, `cap_drop: ALL` and no-new-privileges.

`host.docker.internal` is forwarded to the Mac's loopback by Docker Desktop and by the
`extra_hosts: host.docker.internal:host-gateway` entry in `compose.yaml` for Linux. Whether
it reaches Ollama on this machine has not been verified end-to-end; see USAGE.md for the
manual check.

`make bench-up` starts only the simulators. The triage profile is opt-in and does not
affect bench runs.

## CI chain

Each image is built once, scanned, SBOM'd and saved; every later job uses that exact image.

```
image (build, Trivy, Syft, save, ID) ──► bench
triage-image (same) ──────────────────► triage-smoke
lint, test, secrets ─────────────────┐
image, bench ────────────────────────┤──► publish (main only, packages:write)
triage-image, triage-smoke ──────────┘
bench (if failed) ───────────────────► triage (allowed to fail, never gates publish)
all jobs ────────────────────────────► report (!cancelled(), PRs only)
```

**What leaves the machine:** images are pushed to `ghcr.io` only from pushes to main,
tagged `<version>-<sha7>`. No telemetry. Ollama is called in CI only by the `triage` job,
which runs only when the bench fails.

## Container tests

`make triage-smoke` runs `tests/container/` against the image ID in `TRIAGE_IMAGE`. The
fake Ollama runs from the same triage image with `fake_ollama.py` mounted read-only. Each
test gets its own Docker network and named volume, cleaned up afterwards.

The tests skip with one line when `TRIAGE_IMAGE` is unset or Docker is not running. They
run in the CI `triage-smoke` job and are not part of `make test`.
