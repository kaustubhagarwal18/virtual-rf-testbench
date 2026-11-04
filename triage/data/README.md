# Triage data set

Saved bench runs with known causes, built by `make fault-dataset` (`tools/fault_run.py`).
Each run is a folder:

| File | What it holds |
|---|---|
| `results.json` | The bench suite's results: firmware versions, which tests were selected, how pytest exited, and for each test its outcome, message, instrument errors and measurements with limits and margins |
| `logs.txt` | The last lines of both instruments' JSON logs during the run |
| `sequence.log` | The setup sequence's own log, one line per step. For three faults this is the only place the cause appears. Absent from the `slow_responses` runs, where no session opens, so no step ever runs |
| `label.json` | The known cause: fault, size, category (firmware, bench or test), cause and fix |

- `runs/history/` holds three runs per fault. Triage may retrieve these, with their labels, as
  past failures and how they were fixed.
- `runs/questions/` holds one run per fault. Triage is asked about these, and its answers are
  scored against their labels, which it never sees.

## The faults

Nine, with at least two per category, so no answer can be right by always guessing one.

| Fault | Cause | Sizes in history | Question |
|---|---|---|---|
| `cable_loss` | bench | 0.8, 1.2, 2.0 dB | 1.5 dB |
| `link_down` | bench | nothing to scale | the same |
| `link_flaky` | bench | every 2, 3, 5 sweeps | every 4 |
| `slow_responses` | test | 2.5, 3.0, 4.0 s | 3.5 s |
| `config_parameter_missing` | test | nothing to scale | the same |
| `config_parameter_out_of_range` | test | nothing to scale | the same |
| `config_decimal_error` | test | nothing to scale | the same |
| `reset_leaks_offset` | firmware | nothing to scale | the same |
| `output_command_change` | firmware | nothing to scale | the same |

Where a fault has a size, its question uses one no history run has, so retrieval can't simply
find an identical run. The other faults have no size dimension: their question repeats the
history scenario and differs only in timing and reading noise, which makes them the easier
questions in the set. A write-up has to say so rather than count them as evidence that
retrieval generalises.

## What to know when reading a run

- **The `link_flaky` runs differ from each other**, with 4 to 11 failures depending on how
  often the connector drops a sweep. That is the point of it: the same fault doesn't produce
  the same failures twice.
- **The three configuration faults have identical results files**, 19 errors and one pass.
  Only `sequence.log` tells them apart, which is what makes them worth asking about.
- **The `slow_responses` runs select only the smoke tests.** With every instrument call
  waiting out its timeout, the whole suite would take the best part of an hour per run. The
  results file records what was selected, so a reader can see it.
- **A cable fault doesn't fail the level measurements.** Calibration measures the extra loss
  and compensates for it, so the calibration measurements fail instead (ADR 0006).
- **Three faults leave nothing in `results.json` that explains them.** The configuration
  faults and the leaked offset fail a setup step, and the parameter and reason are in
  `sequence.log` (ADR 0007).

A rebuild builds into a staging folder and replaces both folders only once every run has been
saved, so a bench that fails part-way leaves the previous data set untouched. ADR 0005
explains the faults.
