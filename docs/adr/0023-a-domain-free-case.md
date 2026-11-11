# ADR 0023: The pipeline reads a domain-free case; the evidence's wording comes from the pack

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

After ADR 0021 and 0022 the actions and checks were domain data, but every stage still
read this repository's bench format: `results.json` with its tests, `measurements` and
`firmware_versions`, `docker compose logs` lines, the sequence log's `key=value` lines. And
the evidence spoke RF: "Firmware: …", "Instrument error: …", "Instrument logs", a "No
signal" note and a "no level reading" note were written in code. A payments batch, a wafer
lot or a device fleet could not be triaged without imitating a pytest bench run.

## Decisions

- **A case** (`triage/case.py`): items (tests, checks, transactions, lots) with an outcome,
  a message, errors and readings with limits; setup steps; log lines with a level; and a
  context of component versions, what ran, and where it was read from. It has a JSON form,
  `case.json`, declared as `triage-case/1`, which any system can write.
- **Bench runs are one adapter** (`triage/bench_run.py`). `load_case` opens a folder's
  `case.json` if it has one, and otherwise reads a bench run through the adapter. The
  evidence, the facts, the per-failure split, the API and every tool read cases.
- **The evidence's domain wording is in the pack** (`[evidence]`): the three versions lines,
  the errors line, the name of the logs, and notes: a sentence stated when a fact holds,
  before the first failure (`lead`) or among a focused failure's lines (`focus`). The
  versions line compares with the baseline of the pack's `versions_fact`, so the baseline
  is written once. A pack without `[evidence]` gets neutral wording ("Versions: …",
  "Errors reported: …", "Logs"). The two evaluation ablations leave out the versions line
  and the `no_signal` note by name.
- **The fact kinds drop their field names**, since a case names versions and errors
  itself, and gain `readings_failed_only_outside` for the focused note; `log_contains` also
  gives `{times}` ("once", "3 times").

## Consequences

- Every one of the 78 recorded runs, saved as `case.json` and read back, gives the same
  evidence at every level as through the adapter. The behaviour lock passes unchanged with
  the wording read from the pack, so every prompt is still byte for byte the same.
- The tests triage a payments batch written as `case.json`, with its own pack: its prompt
  and evidence carry payments notes and labels, and no RF word.
- The neutral wording still says "tests" in a few lines ("First failure", "N tests failed
  this way", the selection line). Retrieval and `tools/calibrate_abstention.py` build the
  history's evidence with the default pack, the API accepts the bench format only, and
  `make eval` uses the default pack.
