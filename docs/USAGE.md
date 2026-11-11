# Using the bench and the triage assistant

The README is the overview. This page is the hands-on guide: talking to the instruments,
running the bench and its faults, driving the instruments from Python, and calling the
triage assistant. Setup is in the README's [Reproduce](../README.md#5--reproduce) section.

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
caveat about compound commands is under [Known issues](../README.md#known-issues)). Bad commands queue
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

## Faults

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
| `analyzer_needs_alignment` | bench | The analyzer reads every level high, so the measured path loss falls *below* its limit (a damaged cable pushes it above), and each sweep logs that the alignment is out of date |
| `slow_responses` | test | Every instrument call outlives the session timeout; the instruments report no errors |
| `sweep_timeout` | test | The session opens and answers, but the first sweep (`INIT`) outlives the 2 s timeout |
| `config_parameter_missing` | test | The setup sequence stops at step 1; `sequence.log` names the parameter nobody set |
| `config_parameter_out_of_range` | test | Step 1 again, with a value beyond the generator's range |
| `config_decimal_error` | test | A 15 dB offset where 1.5 dB was meant: step 2 fails, and only the log says so |
| `fixture_skips_reset` | test | A fixture change dropped the `*RST` before each test, so the offset test's offset reaches every test after it: the same failure as `reset_leaks_offset`, on unchanged firmware |
| `reset_leaks_offset` | firmware | `0.2.0-rc2` stops clearing the level offset on `*RST`, so every test after the offset test fails on a setting it never touched |
| `output_command_change` | firmware | `0.2.0-rc1` drops the short `OUTP` form: -113 on a new release |
| `firmware_frequency_error` | firmware | `0.2.0-rc3` puts the output 4 ppm high while `FREQ?` reads back the setting: 4 kHz off at 1 GHz and above, within limits at 100 MHz |

```
make fault NAME=cable_loss SIZE=1.5
```

`make fault` starts the bench on the release each fault needs, so the bench and config faults
run in any order. The three firmware faults (`reset_leaks_offset`, `output_command_change`,
`firmware_frequency_error`)
rebuild a running bench, so start it with `make bench-up` first if it's down. Each run lands
in `results/faults/<fault>-<timestamp>/` with `results.json`,
`logs.txt`, `sequence.log` and a separate `label.json` holding the known cause — what a
closed ticket would carry. A run where nothing failed isn't saved; one pytest couldn't finish
is kept and reported, never counted as a pass.

`make fault-dataset` builds the triage data set in `triage/data/runs/`: three history runs
and three question runs per fault, 78 in all. Where a fault has a size,
each question uses a size that no history run has. ADR 0005 explains the faults, ADR 0007
the sequence log.

## The bench suite

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

## The triage assistant

With local Ollama running and both models pulled:

```
make triage-demo RUN=triage/data/runs/questions/cable_loss-1
```

shows one failure triaged with and without past runs, side by side; it leaves out the
checks and the disagreement gate, to show what the past runs alone add. `make triage RUN=…`
explains one run, and `make eval` scores every scenario; answers are cached in
`.cache/triage-answers/`, so a rerun asks only what changed.

`make triage-api` serves the whole pipeline, checks and disagreement gate included, on
`http://127.0.0.1:8000`. From another terminal:

```
jq -n --slurpfile results triage/data/runs/questions/cable_loss-1/results.json '{results: $results[0]}' | curl -s -X POST http://127.0.0.1:8000/triage -H 'Content-Type: application/json' -d @-
```

The reply is the verdict as JSON: `fix`, `action`, `where`, `because`, `cited`, `category`
(`firmware`, `bench`, `test` or `unknown`) and `past_fixes` (the recorded fix of each cited
run, then of each other offered run resolved by the same action, quoted), plus `overruled` when a check stood in for the model and `gate_reason` when the
past runs disagreed and the model wasn't asked. `POST /history` adds a resolved run (its evidence plus
a `label` with `fault`, `cause`, `fix` and, optionally, `action`), and `GET /health` reports
how many past runs are loaded. Interactive docs are at `http://127.0.0.1:8000/docs`. The
service listens on localhost only and keeps its history in memory (ADR 0010); retrieval is
an exact in-memory search behind a `Retriever` interface, so pgvector can replace it once the
history outgrows memory.
