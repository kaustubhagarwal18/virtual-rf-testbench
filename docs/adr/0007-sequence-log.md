# ADR 0007: A failed setup step names the step, and the sequence log holds the reason

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

Instrument test sequencers report a failed step by number and leave the detail in their own
log file. Someone then opens the log, finds the step, and reads that a parameter was missing,
unset or out of range. That hunt is a large part of the time spent on a failed run, and it is
exactly the work the triage assistant is meant to save.

The project needed failures of that shape to measure against: a generic test failure whose
cause exists only in another artefact.

## Decision

- **Each test's setup runs as a short sequence** (`tests/bench/conftest.py`): read the bench
  configuration, check the generator carries no stale level offset, set the analyzer's
  channel bandwidth.
- **Every step is logged** to `sequence.log`, which is saved with the run beside
  `results.json` and the instruments' logs.
- **A step that fails raises with the step's name only**, for example "Step 2 (verify level
  offset) failed on the generator". The parameter, its value and the reason go to the log.
- **The bench configuration comes from the environment**, so the fault tooling can run the
  suite with a configuration that is wrong the way a person gets it wrong: a parameter nobody
  set, a value beyond the instrument's range, or a value in the wrong unit.

## Consequences

- Four faults produce a failure whose cause is nowhere in `results.json`: the three
  configuration faults and the release that leaks a level offset. The step's name is in the
  test's message, so it narrows the search, but which parameter and why are only in
  `sequence.log`. Two pairs are indistinguishable without it: the missing and out-of-range
  parameters both stop at "read bench configuration", and the decimal error and the leaked
  offset both stop at "verify level offset".
- Nothing is swallowed: the full reason, including any SCPI error, is written to the log, and
  the log is part of the run's evidence. What is withheld is only the test's message, which
  is what makes these failures expensive for a person.
- Triage has to read across artefacts rather than just the results file, and its answers can
  be scored on whether they cited the right line.
