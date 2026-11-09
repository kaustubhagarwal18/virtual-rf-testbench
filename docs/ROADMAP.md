# Roadmap

## Done

### The virtual bench

- Simulated SG-1 signal generator and SA-1 spectrum analyzer, speaking SCPI over TCP with
  the IEEE 488.2 status model and a real error queue.
- A bench physics model: cable loss, analyzer flatness, noise floor and seeded reading noise.
- The analyzer reads the generator's state from its admin port (ADR 0002).
- Drivers on RsInstrument behind a small session interface (ADR 0001, ADR 0003).
- Measurement records with value, limit, margin and firmware versions, written into the
  JUnit XML.
- One hardened simulator image, run with Docker Compose (ADR 0004).
- A calibrated 20-test bench suite against the containers (ADR 0006), with a setup sequence
  whose log names the failed step (ADR 0007), and a JSON results file per run.

### Faults and the data set

- Nine faults across the three causes: bench faults on the admin ports, configuration
  faults in the station's settings, and firmware regressions as simulated releases
  (ADR 0005). `make fault NAME=...` runs one and saves it with its label.
- `make fault-dataset` builds the labelled data set: three history runs and three question
  runs per fault, 54 in all.

### Triage

- The pipeline: evidence led by the first failure, a fixed list of actions, strict parsing
  and a local `qwen2.5:3b` (ADR 0008).
- Retrieval of the most similar resolved runs, in memory, cited by neutral ids (ADR 0009).
- A check that turns a `FIRMWARE` verdict into `UNKNOWN` when the firmware is unchanged.
- The evaluation: five scenarios, each adding one thing, scored as right, `UNKNOWN`,
  confidently wrong and malformed, with a 95% interval and retrieval recall (ADR 0009).
- A FastAPI service (`make triage-api`): `POST /triage`, `POST /history`, `GET /health`
  (ADR 0010).

### CI

- Lint, tests with an 80% coverage floor, the bench suite against the containers, a secret
  scan over the full history, and a results comment on each pull request.
- The image is built once, scanned with Trivy, benched as built, and pushed to GitHub
  Container Registry from `main` (ADR 0011).

## Next, in order

1. **The cluster.** k3s runs the same images through Helm charts, deployed by Argo CD from
   Git, with secrets from OpenBao and observability.
2. **The tag-bump pull request.** Once a chart's values file pins the image, CI opens a pull
   request that bumps it to the tag the publish job just pushed. It waits for the chart: there
   is no file to bump yet.
3. **Triage in CI.** When the bench job fails, a separate job (allowed to fail) triages the
   run with a local model and adds the verdict to the pull request comment.
4. **The rest of the planned triage service.** An ingestion worker, Redis, and Postgres with
   pgvector behind the existing `Retriever` interface, once the history is large enough for
   an in-memory index to matter (ADR 0010).

## Known issues

- Every command in a compound line needs its full path: `CALC:MARK:X?;Y?`, which real
  instruments accept, gives -113 for `Y?`. The drivers always send full paths.
- RsInstrument leaves a socket open when a connection is refused, so Python reports a
  `ResourceWarning`. It comes from the library, not from this code, and does no harm in
  short test runs. ADR 0003 has the details.
- The question runs of the faults with nothing to scale (the configuration faults, the
  firmware faults and `link_down`) repeat their history runs' scenario, so they are easier
  than the sized ones and not fully independent. The evaluation's interval assumes
  independence, so it is, if anything, too narrow.
- The API's history lives in memory: runs added with `POST /history` are gone when the
  service stops.
- Starlette warns that its test client's use of `httpx` is deprecated. The warning comes from
  the library; switching the test dependency is a follow-up.
