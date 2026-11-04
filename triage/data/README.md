# Triage data set

Saved bench runs with known causes, built by `make fault-dataset` (`tools/fault_run.py`).
Each run is a folder:

| File | What it holds |
|---|---|
| `results.json` | The bench suite's results file: firmware versions, selection, each test's outcome, message, instrument errors and measurements |
| `logs.txt` | The last lines of both instruments' JSON logs during the run |
| `label.json` | The known cause: fault, size, category (firmware, bench or test), cause and fix |

- `runs/history/` holds three runs per fault, at different sizes. Triage may retrieve these,
  with their labels, as past failures and how they were fixed.
- `runs/questions/` holds one run per fault. Triage is asked about these, and its answers
  are scored against their labels, which it never sees.

For the two faults with a size, the question run uses a size no history run has (1.5 dB of
cable loss against 0.8, 1.2 and 2.0; a 3.5 s delay against 2.5, 3.0 and 4.0), so retrieval
can't simply find an identical run. The firmware fault has no size: its question run is the
same release, `0.2.0-rc1`, as its history runs, and differs only in timing and reading noise.
It is therefore the easiest question in the set, and any write-up should say so. Telling two
*different* firmware regressions apart would need a second simulated release.

A rebuild (`make fault-dataset`) builds into a staging folder and replaces both folders only
once every run has been saved, so a bench that fails part-way leaves the previous data set
untouched. ADR 0005 explains the design.
