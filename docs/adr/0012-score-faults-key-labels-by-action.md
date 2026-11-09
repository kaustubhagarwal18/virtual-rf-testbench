# ADR 0012: Score faults, key labels by action, and check every retrieval verdict

- **Status:** Accepted
- **Date:** 2026-11-09
- **Amends:** ADR 0009

## Context

A review of ADR 0009's measurements found four things to change:

- **The interval was over the wrong unit.** A fault's three question runs differ only in
  size, timing and reading noise. In the last report all three got the same answer in each
  of the 45 fault-and-scenario cells. 27 questions carried about as much evidence as 9
  faults, so an interval over 27 was too narrow.
- **CABLE and LINK overlapped.** Both acted on "the cable", and the `link_down` history's
  fix reads "Reconnected the cable". All six link questions cited a LINK precedent and still
  chose `CABLE`: the largest single block of misses.
- **A precedent described its fix but never named the action.** The model had to map
  "Reconnected the cable between the instruments" to a key, and mapped it to `CABLE`.
- **The held-out scenario ran unchecked.** Its `config_decimal_error` answers blamed release
  0.2.0-rc2, a precedent's release, on runs still on 0.1.0. The firmware check would have
  caught it, but that scenario didn't apply it.

## Decisions

- **The fault is the unit scored.** A fault is solved when all of its questions are right.
  The headline is "faults solved, out of 9" with a 95% Wilson interval over 9, beside the
  question counts. The report adds a per-fault table and says how often a fault's questions
  split.
- **Each action names its symptom first.** `CABLE`: "signal present but attenuated";
  `LINK`: "no signal or intermittent signal; the path is open".
- **Labels record the action key.** `tools/faults.py` gives each fault its key, the data
  set's labels store it, and the evaluation scores against it, so there is one answer key
  rather than a second table. A history source ends `Action: LINK`. The API's resolved-run
  label takes it as an optional field.
- **Three checks, applied in both retrieval-with-checks scenarios.** A `FIRMWARE` verdict
  becomes `UNKNOWN` when it names a release the run didn't run, or when the run's firmware is
  unchanged. A `TIMEOUT` verdict becomes `UNKNOWN` when a failed test reports an SCPI error,
  because a rejected command is not a slow instrument. A check still never picks another
  action.
- **Model answers persist between runs** under `.cache/triage-answers/`, keyed by a hash of
  the model's settings and both prompts. Any change to a prompt is a new key, so the cache
  can't replay an answer to a different question.

## Measurements

`qwen2.5:3b`, temperature 0, seed 42, on the 8 GB M1. `make eval` asked 75 new prompts and
took 26 minutes; a rerun from the cache asks none and takes 22 seconds. All five scenarios
are in `triage/results/rag.md`.

| Scenario | Faults solved (95% interval) | Questions right | `UNKNOWN` | Wrong | Malformed |
|---|---|---|---|---|---|
| error only | 3 of 9 (1 to 6) | 9 of 27 | 0 | 18 | 0 |
| full evidence | 4 of 9 (2 to 7) | 13 of 27 | 0 | 14 | 0 |
| full + retrieval | 8 of 9 (5 to 9) | 24 of 27 | 0 | 0 | 3 |
| full + retrieval + checks | 8 of 9 (5 to 9) | 24 of 27 | 0 | 0 | 3 |
| full + retrieval + checks, own fault held out | 3 of 9 (1 to 6) | 11 of 27 | 9 | 7 | 0 |

- **A fault's questions split in 2 of 45 fault-and-scenario cells**, both `link_flaky`
  (1 of 3 with full evidence, 2 of 3 held out). Scoring faults loses little.
- **Retrieval no longer gives a confident wrong answer:** 0 of 27, against 9 in ADR 0009,
  which were the six link questions and the three `slow_responses` ones. `link_flaky` and
  `slow_responses` are now solved, and with its own fault held out `link_down` is solved
  from three `link_flaky` precedents.
- **`link_down` with retrieval is malformed, not wrong.** In both retrieval scenarios the
  model put a source's text in `cited` instead of its id, so the parser refuses all three
  replies. The answer's schema allows any string there; constraining `cited` to the offered
  ids is a candidate follow-up.
- **The checks overruled 9 held-out answers, all wrong:** the three `config_decimal_error`
  and three `slow_responses` answers named a precedent's release (0.2.0-rc2, 0.2.0-rc1) on
  runs still on 0.1.0, and the three `output_command_change` answers chose `TIMEOUT` beside
  -113. In the scenarios with a precedent there was nothing for them to overrule.
- **Wording moves this model.** The error-only evidence didn't change, yet the scenario went
  from 12 to 9 questions right: `config_decimal_error` went from `CONFIGURATION` to a wrong
  action when only the descriptions of `CABLE` and `LINK` changed. Small differences
  between scenarios are within that noise.

## Consequences

- Comparing these scores with ADR 0009's needs care: the unit changed, and so did the
  prompt's description of two actions.
- A label without an action is refused by the evaluation, with the command that rebuilds
  the data set.
