# ADR 0005: Fault injection on the admin ports and as simulated releases

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

The triage assistant has to tell three causes apart: the firmware, the bench and the test.
To measure how well it does, the bench needs failures whose cause is known. The faults must
look like the real thing from the test's side, reaching it only through SCPI, and must not
give the answer away in the evidence.

## Decision

- **Bench faults are switched on each simulator's admin port**, never over SCPI. Each fault
  is a number where 0 means off, set with `FAULT <NAME> <value|OFF>`, listed with `FAULTS?`
  and cleared with `CLEAR`. Each simulator offers only the faults that belong to it:
  - the analyzer: `EXTRA_CABLE_LOSS_DB`, applied to the cable between the instruments, so
    the level arrives lower;
  - the generator: `SLOW_RESPONSE_S`, a delay before each SCPI line is handled. The line
    server waits *before* taking the instrument's lock, so a delayed SCPI line never blocks
    the admin port: `CLEAR` still answers at once, and the analyzer can still read `STATE?`.

  The analyzer gets its own admin port for this (5126, so both simulators can run on one
  machine).
- **Firmware regressions are simulated releases, not switches.** A release named in
  `simulators/signal_generator/releases.py` changes the generator's behaviour. In
  `0.2.0-rc1`, `OUTP ON` gives -113 and only `OUTP:STAT ON` works. The bench is built as that
  release with `FIRMWARE_VERSION=0.2.0-rc1 make bench-up`, so `*IDN?` and every measurement
  record carry the new version, as on a real release candidate. Releases are matched by exact
  name, so a real version bump can't pick up a simulated regression.
- **Faults never appear in the evidence.** Switching a fault is logged at debug level only,
  and `*RST` doesn't clear faults, as it wouldn't fix a damaged cable.
- **Each fault and each effect is its own class.** Adding a fault means adding a definition
  and an effect class, without editing existing branches.
- **Labels live apart from the evidence.** `make fault` saves a run as the suite's
  `results.json`, the instruments' logs and a separate `label.json` (category, cause and fix,
  as a closed ticket would record them). Triage reads the first two and is scored against the
  third. A run in which nothing failed isn't saved.
- **The data set keeps questions apart from history.** Each fault has three history runs,
  which triage may retrieve, and one question run, which it is asked about. For the two
  sized faults the question uses a size absent from history. The firmware fault has no size,
  so its question repeats release `0.2.0-rc1`: it is the easiest question in the set, and a
  write-up has to say so rather than count it as evidence that retrieval generalises.
- **A fault run sets the release it needs.** Applying a bench fault first starts the bench on
  its normal release, so a run left on `0.2.0-rc1` by an earlier firmware run can't be saved
  under a cable-loss label. A rebuild of the data set stages its runs and replaces the old
  one only once every run has been saved.

## Consequences

- Each fault produces its own failure pattern on the bench suite: extra cable loss fails the
  two level measurements with their margins; slow responses make every test time out while
  the instruments report no errors; the release candidate fails every test that switches RF
  output, with -113, on a new firmware version.
- Running the slow-response fault exposed a driver bug: after a timeout, the late reply was
  read as the status byte and escaped as `ValueError`. The session now reports it as a
  timeout and refuses further commands, because a late reply could otherwise be read as the
  answer to the next query.
- The bench suite's start-up check waits up to 10 s, so a slow instrument fails the tests'
  own timeouts instead of looking like a stopped bench.
- Every child process the tooling starts is bounded (building the bench, reading the logs,
  running the suite). A stalled one is reported, so the fault is always switched off again.
- A run pytest couldn't finish, such as one where collection failed, is kept and reported
  rather than being read as "nothing failed" and deleted.
