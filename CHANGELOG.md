# Changelog

This project follows [semantic versioning](https://semver.org/) and stays on `0.x` while
its interfaces can still change. No version has been tagged yet. The simulators report the
version in `pyproject.toml` (`0.1.0`) as their firmware release, and the bench suite expects
it, so the package version moves only with a release.

## Unreleased

### Added

- **Domain packs** (ADR 0021): the actions, their causes and the domain's description move
  from code to `triage/packs/rf-bench.toml`, checked on load. A behaviour lock hashes 231
  parts of triage's behaviour and proves the move changed no prompt, evidence, check or vote.
- **An answer for each distinct failure in CI** (ADR 0020): the failed tests are grouped by
  setup step, or by test function and kind of failure, and each of the first five groups is
  triaged on its own; the comment has one numbered block per failure, naming it, listing its
  tests and saying what to do, and names the failures past the cap. A fifth check turns
  `ALIGNMENT` into `UNKNOWN` when no level or power reading is out of limits.
- **Four faults** (ADR 0019): `analyzer_needs_alignment` (the analyzer reads high and warns
  its alignment is out of date), `sweep_timeout` (only the sweep outlives the timeout),
  `firmware_frequency_error` (release `0.2.0-rc3`, 4 ppm high) and `fixture_skips_reset` (a
  test-code defect: the fixture's `*RST` is skipped, so an offset leaks as in
  `reset_leaks_offset`, on unchanged firmware). The data set has 13 faults and 78 runs, and
  every action on the list resolves at least one.
- **Citations are scored**: the report splits right answers by whether they cite their own
  fault's past runs, another fault's or none, and counts those whose quoted fixes include
  their own fault's.
- **"What to do"** in the CI comment: the action's description from the fixed list.
- **Triage in CI** (ADR 0018): when the bench job fails, a job allowed to fail saves the run
  (`tools/save_failed_run.py`), triages it with a local model as the API does
  (`tools/ci_triage.py`) and adds the verdict to the pull request comment: the action, its
  cause, the reason, and what fixed the cited past runs. Text the logs could steer is shown
  only as inline code.
- **A "No signal" ablation** in `make eval` (`full evidence, no signal line`): 5 of 9
  faults, 11 wrong, against full evidence's 7 and 6, so the README's first finding is
  reproducible rather than quoted from ADR 0017. It needed no new prompts.
- **Guides for other people:** `docs/ADAPTING.md` (triaging your own failures, adding a
  fault, driving real instruments), `CONTRIBUTING.md` (setup, workflow, the checks and the
  project's rules) and GitHub issue forms for bug reports and feature requests.
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

- An answer quotes the recorded fix of each cited past run, then of each other offered run
  resolved by the same action, so a right answer citing a look-alike of another fault still
  shows its own fault's fix: behind the gate, 27 of 27 right answers now do (ADR 0019).
- `open_bench` takes a `reset` flag, and a code-defect fault's restore takes the bench down,
  so a run with the reset skipped can't dirty the next one.
- The server's SCPI delay hook receives the line, so a delay can depend on the command.
- The README's results are one figure, Table 1 drawn as rows of solved-fault dots and
  wrong-answer bars; the fuller chart stays in `docs/EVALUATION.md`. The example answer is
  now one the API gives (`cable_loss-1`): the old one, on `config_decimal_error`, is refused
  by the disagreement gate. `make triage-demo` with no run now shows
  `config_parameter_out_of_range-1`, where retrieval does change the verdict.
- The README reads like a short paper: an abstract, five figures (four new: a system overview,
  the fault catalogue, the findings and the limitations, all with dark-mode styles), one
  results table and the commands to reproduce. The hands-on guide moved to `docs/USAGE.md`,
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

- The demo said "changed the verdict from FIRMWARE to CONFIGURATION to the resolving
  fix"; it now says "…to CONFIGURATION, the resolving fix".
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

- The majority vote is confidently wrong on `fixture_skips_reset` (3 of 39 questions): its
  identical history runs are indexed once, and two look-alike runs outvote it. The gate
  answers `UNKNOWN` there (ADR 0019).
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
