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

`qwen2.5:3b`, temperature 0, seed 42, on the 8 GB M1. All five scenarios are in
`triage/results/rag.md`.

| Scenario | Faults solved (95% interval) | Questions right | `UNKNOWN` | Wrong | Malformed |
|---|---|---|---|---|---|
| error only | 3 of 9 (1 to 6) | 9 of 27 | 0 | 18 | 0 |
| full evidence | 4 of 9 (2 to 7) | 13 of 27 | 0 | 14 | 0 |
| full + retrieval | 5 of 9 (2 to 7) | 15 of 27 | 0 | 9 | 3 |
| full + retrieval + checks | 5 of 9 (2 to 7) | 15 of 27 | 6 | 3 | 3 |
| full + retrieval + checks, own fault held out | 3 of 9 (1 to 6) | 12 of 27 | 5 | 10 | 0 |

**A correction.** The first run of this evaluation reported 8 of 9 faults with retrieval and
no confident wrong answer. Five pairs of history runs are identical (the faults with no size
and no timing in their evidence), and retrieval indexed both copies, so a question was often
offered the same precedent two or three times. A review caught it; retrieval now indexes
identical runs once, as adding a run through the API already did. 18 retrieval prompts
changed, and the score fell to 5 of 9.

- **A fault's questions split in 3 of 45 fault-and-scenario cells.** Scoring faults loses
  little.
- **One look-alike precedent outvotes two of the question's own fault.** Retrieval offers
  `config_decimal_error` two of its own runs and one `reset_leaks_offset`, and the reverse;
  `slow_responses` gets one `output_command_change`. In all nine questions the model
  followed the odd one out.
- **Three of the five faults retrieval solves repeat their history runs** (no size to vary),
  so they show recall rather than generalisation. The report now says so.
- **`link_flaky` is solved with retrieval, and `link_down` held out** (from three
  `link_flaky` precedents): the symptom wording and action keys ended the CABLE answers on
  link faults.
- **`link_down` with retrieval is malformed, not wrong.** The model put a source's text in
  `cited` instead of its id, so the parser refuses all three replies. Constraining `cited`
  to the offered ids is a candidate follow-up.
- **The checks overruled 6 wrong answers with retrieval and 5 held out**, all wrong to begin
  with: mostly a `FIRMWARE` verdict naming a precedent's release (0.2.0-rc1, 0.2.0-rc2) on a
  run still on 0.1.0.
- **Wording moves this model.** The error-only evidence didn't change, yet the scenario went
  from 12 to 9 questions right when only the descriptions of `CABLE` and `LINK` changed.

## Consequences

- Comparing these scores with ADR 0009's needs care: the unit changed, and so did the
  prompt's description of two actions.
- A label without an action is refused by the evaluation, with the command that rebuilds
  the data set.
