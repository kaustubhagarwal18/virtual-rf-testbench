# virtual-rf-testbench

An automated RF test bench that runs regression tests against simulated instruments, plus an
assistant that says whether a failure came from the firmware, the bench or the test.

[![CI](https://github.com/kaustubhagarwal18/virtual-rf-testbench/actions/workflows/ci.yml/badge.svg)](https://github.com/kaustubhagarwal18/virtual-rf-testbench/actions/workflows/ci.yml)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Status: early development](https://img.shields.io/badge/status-early%20development-orange)

## The problem

Instrument software teams release new firmware regularly, and every release must pass a
regression suite on test benches before it ships. When a test fails, someone has to work out
which of three things went wrong:

| Source | What it looks like | What it means |
|---|---|---|
| **Firmware** | A real regression | Blocks the release |
| **Bench** | A degraded cable, a stale calibration, a misbehaving instrument | Fix the bench, rerun |
| **Test code** | A timeout that's too tight, a flaky test | Fix the test, rerun |

That triage is slow and manual. The knowledge of what fixed a similar failure last time lives
in people's heads or in old tickets.

## What this project does about it

- **A virtual bench.** Simulated instruments (a signal generator and a spectrum analyzer that
  speak SCPI over TCP) play the firmware under test. Each new simulator version is a
  "release".
- **A regression suite with real measurement discipline.** Every measurement has a limit and
  a margin, the bench is calibrated before measuring, and the instrument error queue is
  checked after every command group.
- **A triage assistant.** It answers "Is this the firmware, the bench or the test, and what
  fixed it last time?", citing the past runs and notes it drew on.

```mermaid
flowchart LR
    release[New simulator version] --> suite[Regression suite]
    suite -->|pass| ship[Release]
    suite -->|fail| triage[Triage assistant]
    triage --> firmware[Firmware regression]
    triage --> bench[Bench problem]
    triage --> test[Test code problem]
```

Faults are injected on purpose (extra cable loss, a level offset, slow responses, a
behaviour change in a new version), so every failure has a known cause. That lets the
assistant's accuracy be measured rather than guessed.

## How the triage assistant works

When the bench suite fails, the assistant reads the saved run and answers one question:
**is this the firmware, the bench or the test, and what fixed it last time?**

| | |
|---|---|
| **Answers with** | one action from a fixed list (`CABLE`, `LINK`, `ALIGNMENT`, `CONFIGURATION`, `TIMEOUT`, `TEST_CLEANUP`, `FIRMWARE`), or `UNKNOWN`, plus where to act, why, and the past runs it cites |
| **Models** | `qwen2.5:3b` answers, `nomic-embed-text` embeds; both on local [Ollama](https://ollama.com), temperature 0, fixed seed |
| **Privacy** | nothing leaves the machine; the model gets no tools, and logs are treated as untrusted data |
| **Measured on** | 27 failed runs of 9 known faults, scored per fault, with a 95% interval |

### The pipeline

<p align="center">
  <img src="docs/images/triage-pipeline.svg" width="860" alt="Triage pipeline: a failed run becomes evidence; retrieval (RAG) finds the three most similar resolved runs and answers UNKNOWN if they disagree; otherwise qwen2.5:3b picks one action, checks refuse a verdict that contradicts the run, and the result is a verdict or UNKNOWN with the reason.">
</p>

**Retrieval is what turns a guess into a lookup.** The failure is embedded and matched
against past runs that record how each was resolved. The three closest travel into the
prompt under neutral ids (`past-3fa2c1d0`, never a folder name that would give the answer
away), and the model may cite only those ids.

### Guardrails against a confident wrong answer

A wrong action costs an engineer hours; `UNKNOWN` costs a minute. Every layer below can only
turn an answer into `UNKNOWN` or refuse it, never pick a different action.

| Layer | What it does | Measured effect |
|---|---|---|
| **Fixed action list** | the model picks a key, so scoring is an exact match | no judge model needed |
| **Reply schema** | one action from the enum; `cited` limited to the offered ids; 512-token cap | an invented citation can't be written |
| **Strict parser** | anything unusable is counted as malformed, never read generously | 3 of 27 with retrieval, all `link_down` |
| **Disagreement gate** | top 3 past runs resolved different ways → `UNKNOWN`, model not asked | wrong answers 3 → 0, at a cost of 3 right ones |
| **Checks** | `FIRMWARE` on a release the run didn't run, or on unchanged firmware; `TIMEOUT` beside an SCPI error | turned 3 wrong answers into `UNKNOWN`, cost none |

### Results

<p align="center">
  <img src="docs/images/triage-results.svg" width="860" alt="Faults solved out of 9: error only 3, full evidence 5, without the version line 4; with retrieval 6, with checks 6, with the disagreement gate 5 and no wrong answers; a majority vote over the same past runs, with no model, 9 with no wrong answers, and 6 with the gate; with the question's own fault held out, 4 with checks (the vote 3) and 1 with both gates (the vote the same).">
</p>

Counts, not percentages: with 9 faults, "6 of 9, 3 to 8" is honest where "67%" is not.
Every question's answer is in [`triage/results/rag.md`](triage/results/rag.md).

### What the numbers say

- **Counting the past runs' actions beats the model on this data set.** A majority vote over
  the same top 3, with no model, solves 9 of 9 with no wrong answers, against the model's 6.
  Behind the gate the model never departs from the precedents: its 5 against the vote's 6
  is 3 malformed replies. This is recall: every question had a past run of its own fault.
  The model's one gain is a new fault where the precedents tie (ADR 0015).
- **Retrieval helps most when the fault has happened before:** 5 → 6 of 9, and wrong answers
  11 → 6. With the fault held out, it drops to 4 of 9, even with the checks.
- **A small model follows the odd precedent out.** Offered two past runs of its own fault
  and one look-alike, it often copies the look-alike. That is why the gate exists.
- **Wording matters.** "Firmware: … (unchanged)" pulled the model to `FIRMWARE`. Saying
  "Instruments: … the same as the last passing run" solved one more fault and lost none.
- **Four of the six solved faults are recall, not reasoning:** their runs have no size to
  vary, so a question repeats a past run almost word for word. The report says so.
- **Two ideas didn't pay off:** leading the evidence with the SCPI error changed no score,
  and a similarity threshold gave up 6 right answers to prevent 3 wrong ones.

### How the evaluation got more honest

Each review found a way the score could flatter the assistant, and closed it.

| ADR | What changed | Retrieval score after it |
|---|---|---|
| [0008](docs/adr/0008-triage-design.md) | fixed action list, one prompt for every scenario | 6 of 9 questions |
| [0009](docs/adr/0009-honest-triage-evaluation.md) | neutral source ids (the folder name gave the answer), 27 questions, a held-out scenario | 18 of 27 questions |
| [0012](docs/adr/0012-score-faults-key-labels-by-action.md) | score faults, not questions; label each run's action; **index identical runs once** (they inflated 5 → 8 of 9) | 5 of 9 faults |
| [0013](docs/adr/0013-abstain-from-retrieval.md) | gates before the model; an ablation of the firmware line | 5 of 9 faults |
| [0014](docs/adr/0014-api-gate-cited-ids-version-wording.md) | the disagreement gate in the API, cited ids in the schema, version wording | **6 of 9 faults** |
| [0015](docs/adr/0015-majority-vote-baseline.md) | a majority vote over the same past runs, with no model, as a baseline | 6 of 9; the vote 9 of 9 |

### Try it

With local Ollama running and both models pulled (see [Quick start](#quick-start)):

```
make triage-demo RUN=triage/data/runs/questions/cable_loss-1
```

shows one failure triaged with and without past runs, side by side. `make triage RUN=…`
explains one run, and `make eval` scores all eight scenarios; answers are cached in
`.cache/triage-answers/`, so a rerun asks only what changed.

`make triage-api` serves the same pipeline on `http://127.0.0.1:8000`. From another terminal:

```
jq -n --slurpfile results triage/data/runs/questions/cable_loss-1/results.json '{results: $results[0]}' | curl -s -X POST http://127.0.0.1:8000/triage -H 'Content-Type: application/json' -d @-
```

The reply is the verdict as JSON: `fix`, `action`, `where`, `because`, `cited`, plus
`overruled` when a check stood in for the model and `gate_reason` when the past runs
disagreed and the model wasn't asked. `POST /history` adds a resolved run (its evidence plus
a `label` with `fault`, `cause`, `fix` and, optionally, `action`), and `GET /health` reports
how many past runs are loaded. Interactive docs are at `http://127.0.0.1:8000/docs`. The
service listens on localhost only and keeps its history in memory (ADR 0010); retrieval is
an exact in-memory search behind a `Retriever` interface, so pgvector can replace it once the
history outgrows memory.

## Status

Built and running: the simulated instruments, their drivers, measurement records, and a
calibrated 20-test bench suite against the containers, all run in CI; a simulator image that
CI scans with Trivy and publishes from `main`; fault injection with a labelled data set of
known-cause failures; and a triage assistant, on the command line and over HTTP, that names
the likely fix, citing the past runs it drew on, with its accuracy measured locally on those
faults. Still to come is the deployment to a cluster. The
[roadmap](docs/ROADMAP.md) has the plan, and [ARCHITECTURE.md](docs/ARCHITECTURE.md) explains
how the pieces fit together.

## Quick start

You need git, `make`, [uv](https://docs.astral.sh/uv/) and Docker. On macOS, install uv with
Homebrew and start Docker Desktop:

```
brew install uv
```

On Linux, follow uv's [installation guide](https://docs.astral.sh/uv/getting-started/installation/)
and make sure the Docker daemon is running. uv installs Python 3.13 for the project, so the
system Python doesn't matter.

Clone the repository and install the dependencies:

```
git clone https://github.com/kaustubhagarwal18/virtual-rf-testbench.git
cd virtual-rf-testbench
make setup
```

Run the tests, which need no Docker:

```
make test
```

Start the bench in Docker, run the bench suite against it, and print its results:

```
make bench-up
make bench-test
make bench-summary
```

The summary shows 20 tests passed. Each reading, with its limit and margin, is in
`reports/bench-junit.xml`. Stop the bench when you're done:

```
make bench-down
```

Triage is scored against the labelled failures with local [Ollama](https://ollama.com)
models, writing `triage/results/rag.md`. Start Ollama and pull both the answering model and
the embedding model (retrieval needs the latter) first:

```
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
make eval
```

It's a measurement needing local models, not a CI gate, so it isn't part of `make test`.

The sections below go further: talking to the instruments by hand, driving them from
Python, and the development commands.

## Run the simulators

Each simulated instrument speaks SCPI over TCP, as networked instruments do. The signal
generator also reports its state as JSON on an admin port (5125), which the spectrum analyzer
reads to know what it is measuring. Start each in its own terminal; on one machine the
analyzer needs a different SCPI port:

```
uv run python -m simulators.signal_generator
```

```
uv run python -m simulators.spectrum_analyzer --scpi-port 5026
```

From a third terminal, switch the generator on and measure it with the analyzer:

```
printf 'FREQ 1 GHz\nPOW -10\nOUTP ON\n' | nc -w 1 127.0.0.1 5025
printf 'CALC:MARK:MAX\nCALC:MARK:X?;CALC:MARK:Y?\n' | nc -w 1 127.0.0.1 5026
printf 'CALC:MARK:FUNC:POW:RES? CPOW\n' | nc -w 1 127.0.0.1 5026
```

The analyzer reads about -11.5 dBm at 1 GHz: the generator's -10 dBm, minus 1.5 dB of cable
loss, plus a small flatness error and reading noise.

| Instrument | Commands |
|---|---|
| Both | `*IDN?`, `*RST`, `*CLS`, `*OPC?`, `*ESE`, `*SRE`, `*ESR?`, `*STB?`, `SYST:ERR?` |
| Signal generator (SG-1) | `[SOURce]:FREQuency` (9 kHz to 6 GHz), `[SOURce]:POWer` (-120 to +20 dBm), `OUTPut` (ON or OFF) |
| Spectrum analyzer (SA-1) | `[SENSe]:FREQuency:CENTer` and `:SPAN`, `[SENSe]:BANDwidth` (RBW), `INITiate` and `INITiate:CONTinuous`, `CALCulate:MARKer:MAXimum`, `:X?` and `:Y?`, channel power with `[SENSe]:POWer:ACHannel:BANDwidth` and `CALCulate:MARKer:FUNCtion:POWer:RESult? CPOWer` |

Several queries on one line get one reply line joined by `;`, as IEEE 488.2 requires (one
caveat about compound commands is under [Known issues](#known-issues)). Bad commands queue
standard SCPI errors instead of failing silently, and if the analyzer can't reach the
generator it measures no signal, as with an unplugged cable, and logs a warning. Logs go to
stderr as one JSON object per line. `--help` lists each simulator's options, and
`SIMULATOR_FIRMWARE_VERSION` sets the version `*IDN?` reports.

## Run the bench in Docker

The same two instruments run as containers from one image, which is how CI runs them. With
Docker Desktop running:

```
make bench-up
```

It builds the image, starts both instruments, and returns once each answers `*IDN?`. They
listen on localhost only: the generator on 5025 (admin port 5125) and the analyzer on 5026
(admin port 5126).
The `nc` and Python examples on this page work against them unchanged.

```
make bench-logs
make bench-down
```

Each container runs as an unprivileged user on a read-only filesystem, and reports the
image's version in `*IDN?`. The image carries no `pip`, and CI scans it with Trivy. ADR 0004 explains the choices.

### Faults

Faults make the bench fail the way real benches do, with a known cause. Bench faults are
switched on the admin ports, never over SCPI:

```
printf 'FAULT EXTRA_CABLE_LOSS_DB 1.0\n' | nc -w 1 127.0.0.1 5126
printf 'FAULT SLOW_RESPONSE_S 2.5\n' | nc -w 1 127.0.0.1 5125
```

`FAULTS?` lists the active and available faults, and `CLEAR` switches them all off. A
firmware regression is a simulated release instead: build the bench as `0.2.0-rc1`, in which
`OUTP ON` gives -113 and only `OUTP:STAT ON` works:

```
FIRMWARE_VERSION=0.2.0-rc1 make bench-up
```

`make fault` does all of this for one fault from the catalogue: it switches the fault on,
runs the bench suite, saves the run, and restores the bench.

| Fault | Cause | What the suite shows |
|---|---|---|
| `cable_loss` | bench | Calibration is out of limits at every frequency; the levels, being calibrated, still pass |
| `link_down` | bench | Nothing connected: 12 measurements read the noise floor, and each sweep logs a warning |
| `link_flaky` | bench | A loose connector drops one sweep in four: a few failures with nothing wrong in their own numbers |
| `slow_responses` | test | Every instrument call outlives the session timeout; the instruments report no errors |
| `config_parameter_missing` | test | The setup sequence stops at step 1; `sequence.log` names the parameter nobody set |
| `config_parameter_out_of_range` | test | Step 1 again, with a value beyond the generator's range |
| `config_decimal_error` | test | A 15 dB offset where 1.5 dB was meant: step 2 fails, and only the log says so |
| `reset_leaks_offset` | firmware | `0.2.0-rc2` stops clearing the level offset on `*RST`, so every test after the offset test fails on a setting it never touched |
| `output_command_change` | firmware | `0.2.0-rc1` drops the short `OUTP` form: -113 on a new release |

```
make fault NAME=cable_loss SIZE=1.5
```

`make fault` starts the bench on the release each fault needs, so the bench and config faults
run in any order. The two firmware faults (`reset_leaks_offset`, `output_command_change`)
rebuild a running bench, so start it with `make bench-up` first if it's down. Each run lands
in `results/faults/<fault>-<timestamp>/` with `results.json`,
`logs.txt`, `sequence.log` and a separate `label.json` holding the known cause — what a
closed ticket would carry. A run where nothing failed isn't saved; one pytest couldn't finish
is kept and reported, never counted as a pass.

`make fault-dataset` builds the triage data set in `triage/data/runs/`: three history runs
and three question runs per fault, 54 in all, in about 8 minutes. Where a fault has a size,
each question uses a size that no history run has. ADR 0005 explains the faults, ADR 0007
the sequence log.

### The bench suite

With the bench up, run the suite in `tests/bench/` against the containers:

```
make bench-test
make bench-summary
```

The suite treats the instruments as black boxes, reaching them only over SCPI through the
drivers. It calibrates the bench's path loss first and judges every level against it
(ADR 0006), then checks identity and release, reset defaults, level and frequency across the
band, channel power, the noise floor, the second harmonic, output switching, error handling
and recovery from a dropped connection — 20 tests in all. Each reading is recorded with its
limit and margin in `reports/bench-junit.xml`, and every run also writes
`reports/bench-results.json`: the firmware versions, each test's outcome and message, the
instrument errors, and every measurement. Tests are marked `smoke`, `regression` or
`negative`, so `uv run pytest tests/bench -m smoke --no-cov` runs just the quick checks. If
the bench isn't running the suite stops at once and says to run `make bench-up`; set
`BENCH_HOST`, `BENCH_GENERATOR_PORT` and `BENCH_ANALYZER_PORT` to point it at another bench.

## Drive the instruments from Python

The drivers in `drivers/` control the instruments the way the bench suite does: over SCPI
through R&S's RsInstrument library, each command group synchronised on `*OPC?` and every
instrument error raised as `InstrumentError`. With both simulators running as above, save
this as `measure.py`:

```python
from drivers.session import RsInstrumentSession, socket_resource
from drivers.signal_generator import SignalGenerator
from drivers.spectrum_analyzer import SpectrumAnalyzer

with (
    RsInstrumentSession(socket_resource("127.0.0.1", 5025)) as generator_session,
    RsInstrumentSession(socket_resource("127.0.0.1", 5026)) as analyzer_session,
):
    generator = SignalGenerator(generator_session)
    analyzer = SpectrumAnalyzer(analyzer_session)
    generator.configure(frequency_hz=1e9, level_dbm=-10.0)
    generator.set_output(on=True)
    analyzer.configure(centre_hz=1e9, span_hz=1e6, rbw_hz=1e3)
    print(analyzer.measure_peak())
```

Then run it:

```
uv run python measure.py
```

Bench tests turn each reading into a `Measurement` from `measurements/record.py`: the value,
its limit, the margin, pass or fail, and both instruments' firmware versions. Each one is
written into the JUnit XML, so a failure always says by how much it missed.

## Development

You need [uv](https://docs.astral.sh/uv/) and `make`. uv installs Python 3.13 for the project,
so it doesn't matter which Python your system has.

```
make setup
make lint
make test
```

| Command | What it does |
|---|---|
| `make setup` | Installs Python 3.13 and the dependencies, and installs the pre-commit hooks |
| `make lint` | Runs ruff, mypy (strict), pylint, pydoclint (docstring Args, Returns and Raises), bandit and codespell, checks the Makefile with mbake and the Dockerfile with hadolint |
| `make fmt` | Formats the code and the Makefile, and applies safe lint fixes |
| `make test` | Runs the tests and fails if coverage is below 80%; writes `reports/junit.xml` and `reports/coverage.xml` |
| `make summary` | Prints a results summary from those files: tests run, passed, failed and skipped per suite, coverage, failures and the slowest tests |
| `make bench-up` / `bench-down` / `bench-logs` | Starts, stops or follows the containerised bench |
| `make bench-test` / `bench-summary` | Runs the bench suite against the containers, writing `reports/bench-junit.xml` and `reports/bench-results.json`, and prints its summary |
| `make fault NAME=… [SIZE=…]` | Runs the bench suite with one fault on and saves the run with its label in `results/faults/` |
| `BENCH_LEVEL_OFFSET_DB`, `BENCH_CHANNEL_BANDWIDTH_HZ` | The bench configuration the setup sequence applies; unset means a healthy bench |
| `make fault-dataset` | Rebuilds the triage data set in `triage/data/runs/`: every fault, three history runs and three question runs each |
| `make triage RUN=…` | Explains one saved run: names the likely cause and fix from its evidence alone, without retrieving past runs (needs a local Ollama model) |
| `make triage-demo RUN=…` | Triages one run with and without retrieval, side by side, to show what the past runs add (needs local Ollama answering and embedding models) |
| `make triage-api` | Serves triage over HTTP on localhost:8000: `POST /triage`, `POST /history`, `GET /health` (needs local Ollama answering and embedding models) |
| `make image` / `image-scan` | Builds the simulator image, or scans it with Trivy as CI does (needs Trivy installed) |
| `make eval [MODEL=…]` | Scores the triage against the labelled question runs and writes `triage/results/rag.md` (needs local Ollama answering and embedding models) |
| `make clean` | Removes the virtual environment, the tool caches and the pre-commit hook; `make setup` restores them |

The pre-commit hooks run `make lint` before every commit, so the same checks run locally and
in CI.

### Continuous integration

GitHub Actions runs on every pull request and on every push to `main`:

| Job | What it checks |
|---|---|
| Lint | Every pre-commit hook on all files: trailing whitespace, final newlines, YAML and TOML syntax, large files, merge conflict markers, then `make lint` |
| Test (Python 3.13) | `make test`, including the 80% coverage floor, then `make summary`, whose table appears on the run's summary page |
| Image (build and scan) | Builds the simulator image once and scans it with Trivy 0.76.1 (pinned, checksum-verified); fails on a fixable high or critical vulnerability |
| Bench (containers) | Loads that same image, `make bench-up BENCH_BUILD=--no-build`, `make bench-test`, then `make bench-summary` on the run's summary page; shows the simulator logs if anything failed |
| Publish image | On pushes to `main` only, once every other job passed: pushes the scanned image to GitHub Container Registry as `virtual-rf-testbench-simulators:<version>-<commit>` |
| Secret scan | gitleaks 8.30.1 over the full git history, including merge commits; the download is checksum-verified |
| Results comment | On pull requests, once the other jobs finish: posts one comment with the test and bench results, coverage, any failures and each job's result, and updates that same comment on every push |

Actions are pinned to commit SHAs, and dependencies install from `uv.lock` exactly
(`uv sync --locked`).

## Known issues

- The simulators read every command in a compound line from the root, so the SCPI short
  form that keeps the previous command's path (`CALC:MARK:X?;Y?`) gives -113. Write each
  command in full, or start it with `:`. The drivers always send full paths.
- RsInstrument leaves a socket open when a connection is refused, so a test that connects to
  a closed port shows a `ResourceWarning`. It comes from the library and is harmless in test
  runs; [ADR 0003](docs/adr/0003-rsinstrument-rather-than-plain-pyvisa.md) has the details.

## Further reading

- [Architecture](docs/ARCHITECTURE.md): how the simulators, drivers, measurement records and
  test suites fit together, and where to look in the code
- [Roadmap](docs/ROADMAP.md): what's done and what comes next
- [Decision records](docs/adr/): why the main choices were made

## License

[MIT](LICENSE)
