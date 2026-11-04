# ADR 0004: One hardened simulator image, run with Docker Compose

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

Week 1 runs the bench on Docker Compose (`make bench-up`), and every later stage reuses what
Compose runs: CI service containers in Week 4, Helm charts on k3s in Weeks 5 and 6. The image
is also the firmware release: each simulator version is a release under test.

## Decision

1. **One image for both simulators.** They share almost all their code. Compose chooses the
   module to run (`simulators.signal_generator` or `simulators.spectrum_analyzer`), so there
   is one build, one vulnerability scan and one version to reason about.
2. **The firmware version is baked in.** The `FIRMWARE_VERSION` build argument sets
   `SIMULATOR_FIRMWARE_VERSION` and the image tag, and `*IDN?` reports it. The OCI labels also
   carry the version and the git revision.
3. **Only runtime dependencies ship.** RsInstrument moved to an optional `drivers` extra
   (and the dev group), so the simulator image installs numpy and nothing else. A later
   test-runner image installs the extra.
4. **Hardened by default.** Base images pinned by digest; a non-root user (10001); a
   read-only filesystem; every Linux capability dropped; no privilege escalation; a 256 MiB
   memory limit; no restart policy, so a crash fails visibly.
5. **Healthy means answering SCPI.** `python -m simulators.healthcheck` sends `*IDN?` and
   passes only on a VirtualRF reply. The analyzer starts after the generator is healthy, and
   `make bench-up` returns only when both are.
6. **Ports on localhost only.** SCPI on 127.0.0.1:5025 (generator) and :5026 (analyzer), the
   generator's admin port on 127.0.0.1:5125. Inside the Compose network both instruments use
   5025, and the analyzer reaches the generator as `signal-generator:5125` (ADR 0002).

## Measured

| | Value |
|---|---|
| Image size | 210 MB |
| Signal generator memory | 38 MiB, idle after a measurement |
| Spectrum analyzer memory | 26 MiB, idle after a measurement |
| `make bench-up`, cold build to both healthy | 22 s |

## Consequences

- The bench suite (step 10) tests the released image from outside, as CI and k3s will.
- Week 2's firmware-change fault is a second image tag, and Week 3 adds the triage stack to the
  same Compose file.
- Docker Desktop must be running for `make bench-up`; `make test` stays Docker-free.
