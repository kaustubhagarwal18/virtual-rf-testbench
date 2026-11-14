# ADR 0028 — Triage container image and build-once CI chain

## Context

The triage API (ADR 0026, ADR 0027) can run as a process, but the milestone plan calls for
a container image so it can be deployed under Helm and tested in isolation. The repository
already has a Dockerfile for the simulators. Any change to it must not alter the simulator
image: the bench tests run the scanned image, and if a triage change rebuilds that image,
the bench tests no longer prove what CI scanned.

A new image also needs a CI job that builds, scans and saves it, without duplicating the
scan and save pattern the simulator image job already uses.

## Decision

**One Dockerfile, two images.** A `triage-build` and a `triage-runtime` stage are added
before the existing simulator stages, so `runtime` stays the last stage and the default
target. The triage stages copy nothing that the simulator stages copy:

- `triage-build` runs `uv sync --locked --no-dev --extra api --no-install-project` and then
  `COPY triage ./triage`. The simulator stages copy `simulators`, `drivers` and
  `measurements`, none of which overlap.
- `triage-runtime` uses the same pinned base image and the same hardening (apt upgrade, pip
  removed, uid 10001). It sets `WORKDIR /app`, `PYTHONPATH=/app` and
  `TRIAGE_HISTORY_DB=/data/history.sqlite`, declares `VOLUME /data`, and runs the API on
  port 8000.

The seed history (39 runs, ~1.6 MB at `triage/data/runs/history/`) is baked into the
image. The answer-key questions and the evaluation results are excluded from the build
context by `.dockerignore`. Added runs and embedding vectors are stored on a named volume at
`/data/history.sqlite`.

**Build once, reuse.** Each CI image job builds, scans with Trivy, generates a Syft SBOM,
saves the image with `docker save`, writes its `docker image inspect` ID to `image-id.txt`
and uploads all three as an artifact. Every later job downloads that artifact, loads the tar
and fails if the loaded `.Id` does not match `image-id.txt`. `publish` checks the same ID
before tagging and pushing.

Two local composite actions (`install-scanners`, `load-image`) keep the repeated shell
steps in one place.

## Consequences

**The simulator image ID is stable across this change.** The triage stages copy nothing the
simulator stages copy, so the simulator layers are unaffected. This was verified by building
both images with identical `FIRMWARE_VERSION` and `REVISION` build arguments on main and on
the branch; the IDs and all six RootFS layers matched.

A cold rebuild of either image will produce a different ID regardless of branch, because
`apt upgrade` and compiled Python timestamps change. The ID comparison between jobs is still
meaningful: the same build produces the same ID within one CI run.

**The triage image is 233 MB; the simulators image is 216 MB.** The difference is fastapi,
uvicorn, pydantic and the triage source. pip, rsinstrument, the simulators and the driver
modules are absent from the triage image.

**Container tests** run against the image by its ID, never by tag, against a fake Ollama
that runs from the same image. This keeps the tests independent of a live Ollama.
