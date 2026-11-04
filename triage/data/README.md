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
- `runs/questions/` holds one run per fault, at a size no history run has. Triage is asked
  about these, and its answers are scored against their labels, which it never sees.

Rebuilding replaces both folders. ADR 0005 explains the design.
