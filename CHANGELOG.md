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
- **A firmware check** (`triage/checks.py`): a `FIRMWARE` verdict on firmware that is
  unchanged since the last passing run becomes `UNKNOWN`, with the reason. `make triage` and
  the API apply it.
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

- The simulator image is repinned to the current `python:3.13.18-slim-trixie` digest,
  applies Debian security updates at build time, and no longer contains `pip`. That took
  the first Trivy scan's 9 fixable high vulnerabilities to none.

### Fixed

- A run with no recorded firmware versions was described as "unchanged since the last
  passing run". It is now described as unknown.
- `python -m triage.evaluate` exits 1 with a one-line hint when Ollama can't be reached,
  instead of a traceback.

### Known issues

- The questions of the faults with nothing to scale repeat their history runs' scenario, so
  the questions are not fully independent and the interval is, if anything, too narrow.
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
