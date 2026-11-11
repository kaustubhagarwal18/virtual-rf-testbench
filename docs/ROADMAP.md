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

- Thirteen faults across the three causes: bench faults on the admin ports, configuration
  faults in the station's settings, and firmware regressions as simulated releases
  (ADR 0005). `make fault NAME=...` runs one and saves it with its label.
- `make fault-dataset` builds the labelled data set: three history runs and three question
  runs per fault, 78 in all.

### Triage

- The pipeline: evidence led by the first failure, a fixed list of actions, strict parsing
  and a local `qwen2.5:3b` (ADR 0008).
- Retrieval of the most similar resolved runs, in memory, cited by neutral ids (ADR 0009).
- Five checks that turn a verdict the evidence contradicts into `UNKNOWN` (ADR 0012, ADR 0017,
  ADR 0020).
- The evaluation: thirteen scenarios, each changing one thing, scored per fault as right,
  `UNKNOWN`, confidently wrong and malformed, with a 95% interval over faults and retrieval
  recall (ADR 0009, ADR 0012). Labels record the action key the score is checked against.
- Gates that answer `UNKNOWN` from the precedents alone, a firmware-line ablation, and
  the instrument error first in the evidence, each measured (ADR 0013).
- The disagreement gate in the API, cited ids in the reply schema, and version wording
  that doesn't name firmware when it is unchanged (ADR 0014).
- A majority-vote baseline: five scenarios answered by the precedents' most common action,
  each the twin of a model scenario, so the report shows what the model adds beyond
  retrieval (ADR 0015).
- Answers state the cause (firmware, bench or test) and quote what fixed the past runs behind
  them (ADR 0016): each cited run, then each other offered run resolved by the same action
  (ADR 0019).
- A FastAPI service (`make triage-api`): `POST /triage`, `POST /history`, `GET /health`
  (ADR 0010).

### CI

- Lint, tests with an 80% coverage floor, the bench suite against the containers, a secret
  scan over the full history, and a results comment on each pull request.
- Triage in CI: when the bench job fails, a job allowed to fail triages the saved run as the
  API does and adds the verdict, with what fixed the cited past runs, to the pull request
  comment (ADR 0018). Each distinct failure, up to five per run, gets its own answer
  (ADR 0020).
- The image is built once, scanned with Trivy, benched as built, and pushed to GitHub
  Container Registry from `main` (ADR 0011).

## Next, in order

1. **Triage unit and integration failures, not only bench failures.** CI triages only a
   failed bench run: when a pull request breaks a unit or integration test, the comment lists
   the failure but doesn't explain it. These failures have no measurements, instrument logs
   or firmware versions, so this needs its own evidence (the traceback and the files the pull
   request changed), history runs of such failures to retrieve from, a decision on which
   action they map to, and the Triage job to run when the Test job fails.
2. **Per-failure answers in the HTTP API.** A `POST /triage/failures` endpoint returning one
   answer per distinct failure, as CI does (ADR 0020), with the same cap; `POST /triage`
   stays as it is.
3. **The cluster.** k3s runs the same images through Helm charts, deployed by Argo CD from
   Git, with secrets from OpenBao and observability.
4. **The tag-bump pull request.** Once a chart's values file pins the image, CI opens a pull
   request that bumps it to the tag the publish job just pushed. It waits for the chart: there
   is no file to bump yet.
5. **The rest of the planned triage service.** An ingestion worker, Redis, and Postgres with
   pgvector behind the existing `Retriever` interface, once the history is large enough for
   an in-memory index to matter (ADR 0010).

## Out of scope for now

- **Infrastructure failures as a fourth cause.** Cut to keep the project focused on firmware,
  bench and test; nothing is built. The idea: three faults on compose, labelled
  `infrastructure` (an instrument container OOM-killed, a service name that doesn't resolve,
  and an image tag that doesn't exist), with each run recording the containers' state, which
  the evidence would need to tell an OOM kill from a dropped link.

## Known issues

- CI triages bench failures only. A failed unit or integration test is listed in the pull
  request comment without an explanation; extending triage to them is next on the list.
- A run focused on one failure keeps the whole run's instrument logs, since a log line names
  no test. In CI, a run with two unrelated faults (release `0.2.0-rc3`, frequency 4 ppm
  high, plus a 3 dB alignment error) had its frequency failures answered `ALIGNMENT`, pulled
  by the analyzer's alignment warning, and one answered `UNKNOWN` by the gate; none got
  `FIRMWARE`. A check now refuses `ALIGNMENT` without a failed level reading (ADR 0020).
- Every command in a compound line needs its full path: `CALC:MARK:X?;Y?`, which real
  instruments accept, gives -113 for `Y?`. The drivers always send full paths.
- RsInstrument leaves a socket open when a connection is refused, so Python reports a
  `ResourceWarning`. It comes from the library, not from this code, and does no harm in
  short test runs. ADR 0003 has the details.
- The question runs of the faults with nothing to scale (the configuration faults, the
  firmware faults and `link_down`) repeat their history runs' scenario, so they are easier
  than the sized ones. The fault, not the question, is the unit scored (ADR 0012).
- With retrieval, the model answers `ALIGNMENT` on `link_down`, copying "recalibrated" from
  the past runs' fix; a check turns it into `UNKNOWN` (ADR 0017), so the API gives no answer
  there, where the majority vote would say `LINK`.
- The disagreement gate the API applies costs right answers as well as wrong ones: with a
  precedent available, wrong answers go from 3 to 0 and right answers from 18 to 15
  (ADR 0014).
- The similarity gate refuses more right answers than it prevents wrong ones when the
  question's own fault is held out (6 against 3); ADR 0013 shows why no threshold fixes it.
- `qwen2.5:3b` is sensitive to wording: rewording two action descriptions moved the
  error-only scenario, whose evidence didn't change, from 12 to 9 questions right.
- The API's history lives in memory: runs added with `POST /history` are gone when the
  service stops.
- Starlette warns that its test client's use of `httpx` is deprecated. The warning comes from
  the library; switching the test dependency is a follow-up.
