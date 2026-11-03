# virtual-rf-testbench

An automated RF test bench that runs regression tests against simulated instruments, plus an
assistant that says whether a failure came from the firmware, the bench or the test.

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

## Status

Early development. The first milestone is the simulated instruments, their Python drivers
and the first bench tests, running on Docker Compose. Setup instructions will be added here as
each part starts working.

## License

[MIT](LICENSE)
