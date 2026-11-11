# ADR 0017: A "No signal" line in the evidence, and a check against alignment without a signal

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

With past runs offered, every `link_down` reply was unusable. The model chose `ALIGNMENT`,
put "LINK" in `where`, and kept writing until the 512-token cap cut it off. The analyzer's
log says what happened ("No signal reaching the analyzer: nothing is connected"), but only
among the last few log lines, after a path loss of about 110 dB that reads like a
calibration problem.

## Decisions

- **The evidence states the missing signal first.** When the analyzer logged "No signal
  reaching the analyzer", the full evidence opens with `No signal: the analyzer logged "No
  signal reaching the analyzer" 22 times.` (`no_signal_note`). It counts the log lines kept
  with the run, the same way `instrument_error_note` states an SCPI error. A test checks
  that the phrase is still the one the simulator writes.
- **A fourth check, `AlignmentWithoutSignalCheck`:** an `ALIGNMENT` verdict on a run whose
  analyzer logged no signal becomes `UNKNOWN`. Self-alignment corrects an instrument's own
  measurement error; it can't bring back a signal that never arrives. Like the other
  checks, it never picks a different action.

## Measurements

`make eval`, 30 new prompts (the `link_down` and `link_flaky` runs, and every prompt whose
past runs include one), 874 s on the 8 GB M1. The check needed no new prompts. Faults solved
of 9, then confident wrong answers of 27.

| Scenario | Before | Line only | Line and check |
|---|---|---|---|
| full evidence | 5, 11 wrong | 7, 6 wrong | 7, 6 wrong |
| full evidence, no firmware line | 4, 14 wrong | 6, 9 wrong | 6, 9 wrong |
| + retrieval | 6, 6 wrong, 3 malformed | 6, 9 wrong | 6, 9 wrong |
| + retrieval + checks | 6, 3 wrong, 3 malformed | 6, 6 wrong | 6, 3 wrong |
| + checks + gates (the API) | 5, 0 wrong, 3 malformed | 5, 3 wrong | **5, 0 wrong** |
| own fault held out, + checks | 4, 11 wrong | 4, 12 wrong | 4, 12 wrong |
| own fault held out, + both gates | 1, 3 wrong | 1, 3 wrong | 1, 3 wrong |

- **Without past runs, the line works:** `link_down` and two of three `link_flaky` questions
  go from `CABLE` to `LINK`.
- **With past runs, it doesn't:** the model still answers `ALIGNMENT` on `link_down`, now in
  a reply that parses. It copies "recalibrated" from the past runs' recorded fix
  ("Reconnected the cable between the instruments and recalibrated"). The line alone gave
  the API 3 confident wrong answers where it had 3 unusable ones. The check is what makes
  the change safe: with it, those three are `UNKNOWN`.
- **Past runs now cost the model a fault:** 7 of 9 without them, 6 with them. They fix
  `config_parameter_out_of_range` and mislead it on `link_down` and `reset_leaks_offset`.
- **Held out, with checks, one more question is wrong** (12 against 11): a `link_flaky`
  question now answered `CABLE`.

## Consequences

- The API still gives no action on `link_down`, but an honest `UNKNOWN` with the check's
  reason instead of a 502. The majority vote would answer `LINK` there (ADR 0015).
- The evidence and every source built from a link run changed, so their source ids changed
  too; cached answers for other prompts are unaffected.
- ADR 0015's tables record the numbers before this change; `triage/results/rag.md` has the
  current ones.
- The "Before" column for full evidence is reproducible: `make eval` runs it as the
  `full evidence, no signal line` ablation (5 of 9, 11 wrong).
