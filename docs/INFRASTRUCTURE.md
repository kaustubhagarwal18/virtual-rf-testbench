# Infrastructure

This document covers the container images, how history is stored, running locally with
Compose, the Helm chart, and the CI chain.

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

**Docker Desktop (Mac and Windows):** `host.docker.internal` resolves to the host's loopback
address automatically, so a default Ollama install on `127.0.0.1:11434` is reachable.

**Linux Docker Engine:** `host-gateway` in `compose.yaml` resolves to the host's Docker
bridge address (typically `172.17.0.1`), not `127.0.0.1`. A default Ollama install listens
only on `127.0.0.1`, so the container cannot reach it. Before running `make triage-up` on
Linux, bind Ollama to the bridge interface:

```
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

Or restrict it to the bridge address only (narrower, recommended if the host is shared):

```
OLLAMA_HOST=172.17.0.1:11434 ollama serve
```

The bridge address can be confirmed with `docker network inspect bridge | grep Gateway`.
Whether it reaches Ollama on this machine has not been verified end-to-end; see USAGE.md for
the manual check.

`make bench-up` starts only the simulators. The triage profile is opt-in and does not
affect bench runs.

## Helm chart

`charts/virtual-rf-testbench/` deploys the same two images to Kubernetes (ADR 0029). Nothing
has been deployed with it yet; `make chart-lint` and `tests/unit/charts/` check what it
renders. A release renders:

| Object | Port | The chart's policies allow |
|---|---|---|
| `<release>-generator` Deployment and Service | 5025 (SCPI) | any pod in the namespace |
| `<release>-generator-admin` Service | 5125 (admin) | the analyzer only, when NetworkPolicy is on |
| `<release>-analyzer` Deployment and Service | 5025 (SCPI) | any pod in the namespace |
| `<release>-analyzer-admin` Service | 5126 (admin) | nothing in the cluster when NetworkPolicy is on; use port-forward |
| `<release>-triage` Deployment and Service | 8000 (HTTP) | any pod in the namespace |
| `<release>-triage-history` PersistentVolumeClaim | | the triage pod, at `/data` |

The admin ports carry the fault switches, so they have their own Services and are never part
of an SCPI Service. Every workload runs one replica with the `Recreate` strategy: each
simulator keeps its instrument state in the process, so a rolling update would put two
different instruments behind one Service, and triage's SQLite history is on a
ReadWriteOnce volume. A rollout has a short outage. A simulator proves it is a VirtualRF
instrument (`*IDN?`) once, at start-up; after that readiness and liveness only open the SCPI
port, so an injected `SLOW_RESPONSE_S` never takes an instrument out of its Services.
Pods run as uid 10001 with a read-only root filesystem, no capabilities, no service account
token and seccomp `RuntimeDefault`, as in compose.

**The history claim outlives the release.** It is annotated `helm.sh/resource-policy:
keep`, so `helm uninstall`, and an upgrade that turns `triage.persistence.enabled` off,
leave it in place; Helm stops managing it. Turning persistence off moves `/data` to an
emptyDir, so the kept claim goes unused and new runs are lost with the pod. To delete the
history on purpose, back it up first, then, with `NAMESPACE` and `RELEASE` set:

```
kubectl --namespace "$NAMESPACE" delete pvc "$RELEASE-virtual-rf-testbench-triage-history"
```

Image tags are empty in `values.yaml` and rendering fails until they are set. Each
environment's values file in Git pins them to a `<version>-<sha7>` tag the publish job
pushed. To render locally:

```
helm template vrf charts/virtual-rf-testbench --values charts/virtual-rf-testbench/ci/test-values.yaml
```

**What leaves the cluster:** the triage pod calls Ollama at `triage.ollamaHost`
(`http://host.minikube.internal:11434` by default, the Mac's Ollama seen from minikube).
Nothing else calls out. Egress isn't restricted by the chart.

**NetworkPolicy** is off by default (`networkPolicy.enabled`), since minikube's default
network doesn't enforce it. When on, the chart's policies deny ingress to every pod of the
release except the ports in the table above. That is what these policies allow, not a
guarantee: NetworkPolicies are additive, so another policy in the namespace that selects
these pods can open more, and traffic from a pod's own node, such as the kubelet's probes,
is always allowed.

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

The tests skip with one line when `TRIAGE_IMAGE` is unset. When `TRIAGE_IMAGE` is set but
Docker is not running, they fail immediately so CI cannot silently skip them. They run in the
CI `triage-smoke` job and are not part of `make test`.
