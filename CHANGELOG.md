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
  the history runs alone. The API doesn't apply them.
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

- `CABLE` and `LINK` are described by their symptoms: "signal present but attenuated", and
  "no signal or intermittent signal; the path is open".
- The held-out scenario applies the checks too.
- Full evidence opens with the SCPI errors the failed tests reported, ahead of the timeout
  they caused.
- `triage_run` takes `checks` and `gates` as keyword arguments.

- The simulator image is repinned to the current `python:3.13.18-slim-trixie` digest,
  applies Debian security updates at build time, and no longer contains `pip`. That took
  the first Trivy scan's 9 fixable high vulnerabilities to none.

### Fixed

- A run with no recorded firmware versions was described as "unchanged since the last
  passing run". It is now described as unknown.
- `python -m triage.evaluate` exits 1 with a one-line hint when Ollama can't be reached,
  instead of a traceback.

### Known issues

- With retrieval, the model's answers on `link_down` are unusable: it puts a source's text
  in `cited` instead of its id, so all three are refused as malformed.
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
