# Roadmap

## Done: the virtual bench

- Simulated SG-1 signal generator and SA-1 spectrum analyzer, speaking SCPI over TCP with
  the IEEE 488.2 status model and a real error queue.
- A bench physics model: cable loss, analyzer flatness, noise floor and seeded reading noise.
- The analyzer reads the generator's state from its admin port (ADR 0002).
- Drivers on RsInstrument behind a small session interface (ADR 0001, ADR 0003).
- Measurement records with value, limit, margin and firmware versions, written into the
  JUnit XML.
- A CI results summary on each run page.
- One hardened simulator image, run with Docker Compose (ADR 0004).
- The bench suite against the containers, run in CI.

## Next, in order

1. **Faults and calibration.** `make fault NAME=...` flips fault switches on the admin ports:
   extra cable loss, a level offset, slow responses, and a firmware release that changes a
   command (a second image tag). Path-loss calibration runs before measuring. The suite grows
   to 15 or more tests, and a JSON results file becomes what CI's summary and the triage
   service both read.
2. **The triage service.** A FastAPI API, an ingestion worker, Redis, Postgres with pgvector
   and a local Ollama model, answering "the firmware, the bench or the test?" with cited
   past runs. An evaluation harness measures its accuracy on the labelled faults.
3. **CI gates.** CI runs the simulators as service containers, scans the images with Trivy,
   pushes them to GitHub Container Registry and opens the tag-bump pull request.
4. **The cluster.** k3s runs the same images through Helm charts, deployed by Argo CD from
   Git, with secrets from OpenBao and observability.

## Known issues

- Every command in a compound line needs its full path: `CALC:MARK:X?;Y?`, which real
  instruments accept, gives -113 for `Y?`. The drivers always send full paths.
- RsInstrument leaves a socket open when a connection is refused, so Python reports a
  `ResourceWarning`. It comes from the library, not from this code, and does no harm in
  short test runs. ADR 0003 has the details.
