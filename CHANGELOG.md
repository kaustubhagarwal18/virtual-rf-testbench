# Changelog

This project follows [semantic versioning](https://semver.org/) and stays on `0.x` while
its interfaces can still change. No version has been tagged yet. The simulators report the
version in `pyproject.toml` (`0.1.0`) as their firmware release, and the bench suite expects
it, so the package version moves only with a release.

## Unreleased

### Added

- **A more honest triage evaluation** (ADR 0009). Past runs are cited by a hash of their
  text (`past-3fa2c1d0`) instead of their folder name, which named the fault. The data set
  has three question runs per fault (27, up from 9). Scores are reported as right, `UNKNOWN`,
  confidently wrong and malformed, with a 95% Wilson interval, and retrieval recall is
  reported apart from answer accuracy. Two scenarios are new: one with a firmware check,
  and one with the question's own fault held out of the history.
- **Checks on the verdict** (`triage/checks.py`): a `FIRMWARE` verdict becomes `UNKNOWN`
  when it names a release the run didn't run, or when the firmware is unchanged since the
  last passing run; a `TIMEOUT` verdict becomes `UNKNOWN` when a failed test reports an SCPI
  error. Each gives its reason. `make triage`, the API and both retrieval-with-checks
  scenarios apply them.
- **Per-fault scores** (ADR 0012): the evaluation reports faults solved out of 9, with the
  interval over faults, plus a per-fault table; question counts stay beside them.
- **Action keys in labels.** Every fault label records the action that resolved it
  (`"action": "LINK"`), the evaluation scores against it, and a history source ends
  `Action: LINK`. The API's resolved-run label accepts it as an optional field.
- **Abstention gates** (`triage/abstain.py`, ADR 0013), scored in two new scenarios:
  `UNKNOWN` without asking the model when the precedents record different actions or the
  closest is below 0.95 similarity. `tools/calibrate_abstention.py` sets the threshold from
  the history runs alone. The API applies the disagreement gate only, and says why in
  `gate_reason` (ADR 0014).
- **Cited ids in the schema:** each question's reply schema lists the source ids it was
  offered, and replies are capped at 512 tokens, so a runaway reply ends as malformed
  instead of hanging until the timeout (ADR 0014).
- **A "No signal" line and an alignment check** (ADR 0017). When the analyzer logged that no
  signal reached it, the evidence says so first, with a count; a fourth check turns an
  `ALIGNMENT` verdict on such a run into `UNKNOWN`. Without past runs, full evidence went
  from 5 to 7 of 9 faults; with the gate, wrong answers stay at 0.
- **Answers state the cause and what fixed it last time** (ADR 0016). Every verdict says
  whether its action answers the firmware, the bench or the test (`category`, looked up from
  the action), and quotes the recorded fix of each past run it cites (`past_fixes`), word
  for word from the run's label. `make triage`, `make triage-demo` and the API show both;
  the prompt is unchanged, so no stored answer moved.
- **A majority-vote baseline** (`triage/vote.py`, ADR 0015): five scenarios answer with the
  action most of the top 3 precedents were resolved by, with no model asked, through the
  same gates and checks. On this data set the vote solves 9 of 9 faults against the model's
  6, which shows how much of the score is retrieval.
- **A firmware-line ablation:** an evaluation-only evidence level without the
  "Firmware: …" line, and a scenario that scores it.
- **A persistent answer cache** for `make eval` under `.cache/triage-answers/`, keyed by the
  model's settings and both prompts; `--no-cache` asks every prompt again.
- **Schema-constrained replies.** The answering model is asked through Ollama's structured
  outputs, with the actions as an enum. Replies are still parsed strictly.
- **A triage HTTP API** (ADR 0010): `make triage-api` serves `POST /triage`,
  `POST /history` and `GET /health` on localhost, behind the optional `api` extra.
- **CI image gates** (ADR 0011): the simulator image is built once, scanned with Trivy
  0.76.1 (pinned and checksum-verified), benched as built, and pushed to GitHub Container
  Registry from `main` as `<version>-<commit>`. `make image` and `make image-scan` do the
  same locally.
- `CHANGELOG.md`, and an up-to-date roadmap and architecture document.

### Changed

- The README reads like a short paper: an abstract, four figures (two new: a system overview
  and the fault catalogue, both with dark-mode styles), one results table, findings,
  limitations and the commands to reproduce. The hands-on guide moved to `docs/USAGE.md`,
  and the full command and CI tables to `docs/DEVELOPMENT.md`.
- The README's triage section is shorter: what the assistant answers, one real example, and
  how well it works in three sentences. The guardrails, results chart, findings and ADR
  history moved to `docs/EVALUATION.md`, and the overview's flowchart is gone.
- `CABLE` and `LINK` are described by their symptoms: "signal present but attenuated", and
  "no signal or intermittent signal; the path is open".
- The held-out scenario applies the checks too.
- Full evidence opens with the SCPI errors the failed tests reported, ahead of the timeout
  they caused.
- `triage_run` takes `checks` and `gates` as keyword arguments, and `LLMProvider.complete`
  an optional per-reply schema.
- Unchanged instrument versions read "Instruments: … the same as the last passing run.",
  without the word firmware; it lifted full evidence from 4 to 5 of 9 faults (ADR 0014).
- `triage/evaluate.py` is split: results and scores live in `triage/scores.py`, and the
  report and its findings in `triage/report.py`. A scenario's `retrieve` and `hold_out`
  flags are one `precedents` field. The report's "Adds" column is now "Changes one thing
  in", since a vote scenario replaces the model rather than adding to it.

- The simulator image is repinned to the current `python:3.13.18-slim-trixie` digest,
  applies Debian security updates at build time, and no longer contains `pip`. That took
  the first Trivy scan's 9 fixable high vulnerabilities to none.

### Fixed

- The stalled-log-stream test failed about 2 runs in 500: a writer thread that started late
  freed a queue slot mid-stall, so two drop warnings went out where the test expected one.
  The test now waits until the writer is stuck on its first record before logging the rest,
  and 0 of 2000 runs failed (0 of 500 under CPU load). The logging code was correct and is
  unchanged.
- Retrieval indexed identical history runs once each, so a question could be offered the
  same precedent two or three times; five pairs in the data set are identical. They are
  indexed once now, and the measured retrieval score fell from 8 to 5 of 9 faults
  (ADR 0012).
- The evaluation report now names the faults whose questions repeat their history runs'
  scenario, and how many of the best retrieval scenario's solved faults are among them.

- A run with no recorded firmware versions was described as "unchanged since the last
  passing run". It is now described as unknown.
- `python -m triage.evaluate` exits 1 with a one-line hint when Ollama can't be reached,
  instead of a traceback.

### Known issues

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
- The API's history lives in memory; runs added with `POST /history` are lost on restart.
- The tag-bump pull request waits for a Helm chart to bump.
- Starlette warns that its test client's use of `httpx` is deprecated.

## 0.1.0 (not tagged)

### Added

- Simulated SG-1 signal generator and SA-1 spectrum analyzer over SCPI, with the IEEE 488.2
  status model, an error queue and a bench physics model.
- RsInstrument drivers behind a small session interface, and measurement records with value,
  limit, margin and firmware versions.
- A hardened simulator image on Docker Compose, and a calibrated 20-test bench suite against
  it, with a setup-sequence log and a JSON results file.
- Nine faults across firmware, bench and test, and a labelled data set built from them.
- The triage pipeline on a local `qwen2.5:3b`, the no-retrieval baseline, retrieval of
  similar past runs, and an evaluation harness.
- CI: lint, tests with an 80% coverage floor, the bench suite, a secret scan and a results
  comment on each pull request.

### Known issues

- A compound SCPI line must spell each command's full path (`CALC:MARK:X?;Y?` gives -113).
- RsInstrument leaves a socket open when a connection is refused (a `ResourceWarning`).
