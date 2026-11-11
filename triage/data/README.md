# Triage data set

Saved bench runs with known causes, built by `make fault-dataset` (`tools/fault_run.py`).
Each run is a folder:

| File | What it holds |
|---|---|
| `results.json` | The bench suite's results: firmware versions, which tests were selected, how pytest exited, and for each test its outcome, message, instrument errors and measurements with limits and margins |
| `logs.txt` | The last lines of both instruments' JSON logs during the run |
| `sequence.log` | The setup sequence's own log, one line per step. For four faults this is the only place the cause appears. Absent from the `slow_responses` runs, where no session opens, so no step ever runs |
| `label.json` | The known cause: fault, size, category (firmware, bench or test), cause and fix |

- `runs/history/` holds three runs per fault. Triage may retrieve these, with their labels, as
  past failures and how they were fixed.
- `runs/questions/` holds three runs per fault. Triage is asked about these, and its answers
  are scored against their labels, which it never sees.
- A folder's name says which fault it holds (`cable_loss-2`). The model never sees it: a
  history run is cited by a hash of its text, and a question run only by its evidence.

## The faults

Thirteen, with at least three per category and every action on the list resolving at least
one, so no answer can be right by always guessing one.

| Fault | Cause | Sizes in history | Sizes in questions |
|---|---|---|---|
| `cable_loss` | bench | 0.8, 1.2, 2.0 dB | 1.5, 1.0, 2.5 dB |
| `link_down` | bench | nothing to scale | the same |
| `link_flaky` | bench | every 2, 3, 5 sweeps | every 4, 6, 7 |
| `analyzer_needs_alignment` | bench | 1.0, 2.0, 4.0 dB | 1.5, 3.0, 5.0 dB |
| `slow_responses` | test | 2.5, 3.0, 4.0 s | 3.5, 2.2, 5.0 s |
| `sweep_timeout` | test | 2.5, 3.0, 4.0 s | 3.5, 2.2, 5.0 s |
| `config_parameter_missing` | test | nothing to scale | the same |
| `config_parameter_out_of_range` | test | nothing to scale | the same |
| `config_decimal_error` | test | nothing to scale | the same |
| `fixture_skips_reset` | test | nothing to scale | the same |
| `reset_leaks_offset` | firmware | nothing to scale | the same |
| `output_command_change` | firmware | nothing to scale | the same |
| `firmware_frequency_error` | firmware | nothing to scale | the same |

Where a fault has a size, its questions use sizes no history run has, so retrieval can't simply
find an identical run. The other faults have no size dimension: their question repeats the
history scenario and differs only in timing and reading noise, which makes them the easier
questions in the set. A write-up has to say so rather than count them as evidence that
retrieval generalises.

## What to know when reading a run

- **The `link_flaky` runs differ from each other**, with 4 to 11 failures depending on how
  often the connector drops a sweep (the questions: 10, 8 and 4). That is the point of it: the same fault doesn't produce
  the same failures twice.
- **Two faults have results files that cannot be told apart.**
  `config_parameter_missing` and `config_parameter_out_of_range` both fail at "read bench
  configuration" on the station, with 19 errors and one pass; only `sequence.log` says
  whether the parameter was unset or out of range. `config_decimal_error` and
  `reset_leaks_offset` both fail at "verify level offset" on the generator, which separates
  them from the first two but not from each other — again, only the log says which.
- **The `slow_responses` and `sweep_timeout` runs select only the smoke tests.** With
  every instrument call, or every sweep, waiting out its timeout, the whole suite would take
  far longer per run. The results file records what was selected, so a reader can see it.
- **An alignment fault also fails calibration, from the other side.** The analyzer reads high,
  so the measured path loss falls below its limit where a damaged cable pushes it above; the
  levels, compared against that calibration, still pass.
- **The new faults were added to an existing data set.** The 24 runs of
  `analyzer_needs_alignment`, `sweep_timeout`, `firmware_frequency_error` and
  `fixture_skips_reset` were built on their own, with the same `run_fault` and folder layout,
  so the 54 older runs (and the cached answers about them) stayed as they were.
  `make fault-dataset` rebuilds all 78.
- **`fixture_skips_reset` and `reset_leaks_offset` fail identically.** Both stop at "verify
  level offset" with the offset the offset test left; only the version line differs. The
  defect is switched on by `BENCH_DEFECT_SKIP_RESET`, a hook in
  `tests/utilities/injected_defects.py` that a real suite wouldn't have, and its restore
  takes the bench down so the next run starts from fresh instruments.
- **A fault with identical runs has one past run to retrieve.** The three
  `fixture_skips_reset` history runs are the same run and are indexed once, so its neighbours
  outvote it.
- **A cable fault doesn't fail the level measurements.** Calibration measures the extra loss
  and compensates for it, so the calibration measurements fail instead (ADR 0006).
- **Three faults leave nothing in `results.json` that explains them.** The configuration
  faults and the leaked offset fail a setup step, and the parameter and reason are in
  `sequence.log` (ADR 0007).

A rebuild builds into a staging folder and replaces both folders only once every run has been
saved, so a bench that fails part-way leaves the previous data set untouched. ADR 0005
explains the faults.
