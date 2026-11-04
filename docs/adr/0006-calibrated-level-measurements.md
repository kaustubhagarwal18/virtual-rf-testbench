# ADR 0006: Levels are judged against a measured path loss, not a nominal one

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

The bench suite first compared each level reading with a hard-coded 1.5 dB of cable loss and
a 0.5 dB tolerance. That works for one cable at one frequency and nothing else: the loss
grows with frequency, every bench has a different cable, and the tolerance has to be widened
to cover the cable rather than the measurement.

Real benches calibrate instead. The path from generator to analyzer is measured once, and
every later reading is judged against that measurement.

## Decision

- **A session fixture measures the path loss** at each frequency the suite uses (100 MHz,
  1 GHz, 2.4 GHz, 5 GHz) before any level measurement is judged.
- **Level measurements are judged against the measured loss**, so their tolerance covers only
  the measurement: 0.3 dB rather than 0.5 dB.
- **The calibration result is itself a measurement with a limit**, taken from the cable's
  datasheet: the rated loss at 1 GHz scaled with the square root of frequency, plus or minus
  0.5 dB, and never below zero, because a passive cable cannot have gain.
- **The datasheet figure is written in the test code**, not imported from the simulator's
  physics model. A test that took its expectation from the thing under test would only be
  checking the model against itself, and on a real bench the datasheet is independent
  knowledge.

## Consequences

- A degraded cable now fails **calibration**, not the level measurements, because calibration
  measures the extra loss and compensates for it. A 1.5 dB fault fails all four calibration
  measurements by about 1 dB while every level measurement passes. That is both what a real
  bench does and better evidence: it says the path changed and the instruments did not.
- A level measurement may only be taken at a calibrated frequency; asking for another raises,
  rather than silently comparing against nothing.
- The suite cannot be run usefully as a single test: the calibration fixture is part of
  every level measurement's meaning. A subset run records what it selected, so a reader can
  see that.
