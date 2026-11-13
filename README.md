# virtual-rf-testbench

### A test failed. Was it the firmware, the bench or the test?

A simulated RF bench, and a local AI that names the likely culprit and quotes what fixed it
last time.

[![CI](https://github.com/kaustubhagarwal18/virtual-rf-testbench/actions/workflows/ci.yml/badge.svg)](https://github.com/kaustubhagarwal18/virtual-rf-testbench/actions/workflows/ci.yml)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Status: early development](https://img.shields.io/badge/status-early%20development-orange)
<br>
![Python 3.13](https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white)
![uv 0.13.7](https://img.shields.io/badge/uv-0.13.7-DE5FE9)
![NumPy 2.5](https://img.shields.io/badge/numpy-2.5-013243?logo=numpy&logoColor=white)
![FastAPI 0.162](https://img.shields.io/badge/fastapi-0.162-009688?logo=fastapi&logoColor=white)
![RsInstrument 1.131](https://img.shields.io/badge/rsinstrument-1.131-0a3069)
![Docker Compose](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)
![Ollama qwen2.5:3b](https://img.shields.io/badge/ollama-qwen2.5%3A3b-000000?logo=ollama&logoColor=white)
<br>
![ruff 0.17](https://img.shields.io/badge/ruff-0.17-D7FF64?logo=ruff&logoColor=black)
![mypy strict](https://img.shields.io/badge/mypy-strict-2A6DB2)
![pylint 10/10](https://img.shields.io/badge/pylint-10%2F10-brightgreen)
![pytest 9.2](https://img.shields.io/badge/pytest-9.2-0A9EDC?logo=pytest&logoColor=white)
![coverage 100%](https://img.shields.io/badge/coverage-100%25-brightgreen)
![bandit](https://img.shields.io/badge/security-bandit-yellow)
![pre-commit](https://img.shields.io/badge/pre--commit-enabled-FAB040?logo=precommit&logoColor=white)
![Trivy 0.76.1](https://img.shields.io/badge/trivy-0.76.1-1904DA)
![gitleaks 8.30.1](https://img.shields.io/badge/gitleaks-8.30.1-red)

<p align="center">
  <img src="docs/images/overview.svg" width="860" alt="A new firmware release runs on a virtual bench of a signal generator and a spectrum analyzer. A 20-test regression suite measures it; a pass ships, a failure goes to the triage assistant, which says whether the cause is the firmware, the bench or the test, with what fixed it last time.">
</p>
<p align="center"><sub><b>Figure 1.</b> A release runs on the virtual bench; a failed run goes to the triage assistant.</sub></p>

## Abstract

Instrument firmware ships only after a regression suite passes on a test bench. When a test
fails, an engineer has to decide whether the firmware regressed, the bench is faulty or the
test is wrong, and what fixed a similar failure last time is often in someone's head. This
project builds a simulated RF bench, a 20-test regression suite with limits and margins,
and thirteen faults injected on purpose, so every failure has a known cause. A triage
assistant, running locally, names the cause and quotes what fixed the most similar past
failures. With no evidence at all, a rules-only baseline answers UNKNOWN on every fault:
the floor is 0 of 13. From a run's own evidence it names the right fix for **8 of 13 faults**;
applying checks alone to that evidence doesn't solve more faults but converts wrong answers
to UNKNOWN, so the floor is safer. With past runs and behind its safety gate it solves **9
with no wrong answers**. A majority vote over the retrieved past runs solves 12 but answers
the last one wrong: on this data set, retrieval does most of the work, and the gate is what
keeps it from being confidently wrong. `qwen2.5:7b` matches `qwen2.5:3b`'s ceiling at
12 of 13: a larger model does not help once retrieval is in place.

## 1 · Method

<p align="center">
  <img src="docs/images/triage-pipeline.svg" width="760" alt="Triage pipeline: a failed run becomes evidence; retrieval finds the three most similar resolved runs and answers UNKNOWN if they disagree; otherwise qwen2.5:3b picks one action, checks refuse a verdict that contradicts the run, and the result is a verdict or UNKNOWN with the reason.">
</p>
<p align="center"><sub><b>Figure 2.</b> The triage pipeline. Every guardrail can only turn an answer into <code>UNKNOWN</code>, never pick another action.</sub></p>

- **One action from a fixed list**, so an answer is checked by exact match. The cause
  (firmware, bench or test) is looked up from the action.
- **Retrieval**: the three closest past runs, cited by neutral ids; their recorded fix is
  quoted, never generated.
- **Guardrails**: a gate answers `UNKNOWN` when the past runs disagree, and five rule checks
  refuse a verdict the evidence contradicts.
- **Local and private**: `qwen2.5:3b` and `nomic-embed-text` on Ollama; nothing leaves the
  machine.
- **In CI**: when the bench job fails, each distinct failure (up to five) is triaged on its
  own, and the answers, with what fixed the cited past runs, go into the pull request
  comment.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/ci-triage-comment-dark.png">
    <img src="docs/images/ci-triage-comment-light.png" width="760" alt="The triage section of a pull request comment for a bench run with two injected faults, release 0.2.0-rc3 (frequency 4 ppm high) and a 3 dB analyzer alignment error. 8 failed tests form 4 distinct failures, each with its own block saying what to do. Failure 1, path loss: UNKNOWN, the model was not asked because the closest past runs were fixed differently (ALIGNMENT and CABLE). Failures 2 and 3, level and frequency across the band and level accuracy: FIRMWARE, report a firmware regression and pin the previous release, as three past runs did. Failure 4, recovery after a dropped connection: the model chose ALIGNMENT and a check refused it, since no level or power reading failed, so the answer is UNKNOWN and the advice is to look by hand.">
  </picture>
</p>
<p align="center"><sub>The comment on a CI run with two unrelated faults at once: two failures solved, two left to a person, none wrong (ADR 0020).</sub></p>

## 2 · Data

<p align="center">
  <img src="docs/images/faults.svg" width="860" alt="Thirteen faults: four bench (cable_loss, link_down, link_flaky, analyzer_needs_alignment), six test (slow_responses, sweep_timeout, three configuration errors and fixture_skips_reset), three firmware (reset_leaks_offset, output_command_change, firmware_frequency_error). Three history and three question runs each, 78 in all.">
</p>
<p align="center"><sub><b>Figure 3.</b> Thirteen faults with known causes. The assistant may retrieve the history runs and is scored on the question runs.</sub></p>

## 3 · Results

<p align="center">
  <img src="docs/images/results-table.svg" width="860" alt="Faults solved of 13 and wrong answers of 39. Error message only: 5 (95% interval 2 to 8), 24 wrong. Full evidence: 8 (5 to 11), 15 wrong. Plus the 3 closest past runs and checks: 11 (8 to 12), 3 wrong. Plus the disagreement gate, which the API runs: 9 (6 to 11), 0 wrong. Majority vote of the 3 past runs, no model: 12 (9 to 13), 3 wrong.">
</p>
<p align="center"><sub><b>Table 1.</b> A fault is solved when all three of its questions are right; the small numbers are 95% intervals.</sub></p>

A real answer from the API, on a run whose path loss at 100 MHz is 1.89 dB against a limit of
0.97 dB:

```
fix:   CABLE
where: cable
why:   Extra loss in the cable between generator and analyzer, from a damaged cable
cause: bench
cited: past-72b3edd9, past-22406f9c, past-7858bf7e
last time: Replaced the cable and recalibrated the path loss (past-72b3edd9, past-22406f9c, past-7858bf7e)
```

<p align="center">
  <img src="docs/images/findings.svg" width="860" alt="Finding 1, stating a fact helps: a line saying the analyzer logged no signal took full evidence from 6 to 8 of 13 faults, fixing link_down and link_flaky. Finding 2, past runs help and can mislead: with the three closest past runs, 8 becomes 11; they fix the alignment fault, both timeouts and config_parameter_out_of_range, but link_down becomes ALIGNMENT. Finding 3, safety costs answers: the disagreement gate prevents 3 wrong answers and gives up 6 right ones.">
</p>
<p align="center"><sub><b>Figure 4.</b> Three findings from the experiments.</sub></p>

[docs/EVALUATION.md](docs/EVALUATION.md) has every scenario and what each experiment taught.

## 4 · Limitations

<p align="center">
  <img src="docs/images/limitations.svg" width="860" alt="Four limitations. Small, easy data: 8 of 13 faults repeat a past run, so the vote's 12 of 13 is recall, not reasoning. One small model: qwen2.5:3b at temperature 0, one seed, on an 8 GB laptop; a one-fault difference may be noise. One fault unanswered: with past runs the model wants ALIGNMENT on link_down and a check refuses it, where the vote would say LINK. Simulated bench: no real instruments; noise and errors are modelled, not measured.">
</p>
<p align="center"><sub><b>Figure 5.</b> What these results do not show.</sub></p>

## 5 · Reproduce

You need git, `make`, [uv](https://docs.astral.sh/uv/) and Docker. On macOS:

```
brew install uv
git clone https://github.com/kaustubhagarwal18/virtual-rf-testbench.git
cd virtual-rf-testbench
make setup
make test
```

Run the 20-test suite against the bench in Docker:

```
make bench-up
make bench-test
make bench-down
```

Score the triage assistant and triage one run, with [Ollama](https://ollama.com) running:

```
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
make eval
make triage-demo RUN=triage/data/runs/questions/cable_loss-1
```

On Linux, install uv from its [guide](https://docs.astral.sh/uv/getting-started/installation/)
and start the Docker daemon. [docs/USAGE.md](docs/USAGE.md) covers the instruments, faults,
Python drivers and the HTTP API.

## Development

```
make lint
make test
```

`make lint` runs ruff, mypy (strict), pylint, pydoclint, bandit, codespell, hadolint on the
Dockerfile and mbake on the Makefile; `make test` enforces 80% coverage.
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) lists every command and CI job.

## Known issues

- Compound SCPI lines need full paths: `CALC:MARK:X?;Y?` gives -113 for `Y?`. The drivers
  always send full paths.
- RsInstrument leaves a socket open when a connection is refused, so Python shows a harmless
  `ResourceWarning` ([ADR 0003](docs/adr/0003-rsinstrument-rather-than-plain-pyvisa.md)).

## Documentation

<table>
<tr>
<td width="33%" valign="top">

<b>Use it</b><br><br>
<a href="docs/USAGE.md"><b>Usage</b></a><br>
<sub>Instruments, Docker bench, faults, Python drivers, HTTP API</sub><br><br>
<a href="docs/ADAPTING.md"><b>Adapting</b></a><br>
<sub>Triage your own failures, add a fault, drive real instruments</sub><br><br>

</td>
<td width="33%" valign="top">

<b>Understand it</b><br><br>
<a href="docs/EVALUATION.md"><b>Evaluation</b></a><br>
<sub>Every scenario, the guardrails, and what each experiment taught</sub><br><br>
<a href="docs/ARCHITECTURE.md"><b>Architecture</b></a><br>
<sub>How the pieces fit, and where to look in the code</sub><br><br>
<a href="docs/adr/"><b>Decision records</b></a><br>
<sub>Why the main choices were made</sub><br><br>

</td>
<td width="33%" valign="top">

<b>Build on it</b><br><br>
<a href="docs/ROADMAP.md"><b>Roadmap</b></a><br>
<sub>What's done and what comes next</sub><br><br>
<a href="CONTRIBUTING.md"><b>Contributing</b></a><br>
<sub>Setup, workflow, the checks and the project's rules</sub><br><br>

</td>
</tr>
</table>

## License

[MIT](LICENSE)
