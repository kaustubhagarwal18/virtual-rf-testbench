# ADR 0019: Quote every past run behind the action, score citations, and add four faults

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

In CI, a bench run with an 80 dB level offset got the right action, `CONFIGURATION`, but its
"last time" quoted the fix of another fault: "Set level_offset_db in the bench
configuration", the fix for a *missing* offset. The model had cited a look-alike run of
`config_parameter_missing`, though two runs of its own fault were among the three offered.
The evaluation scored only the action, so it couldn't see this. The data set also had no
fault resolved by `ALIGNMENT` or `TEST_CLEANUP`, and only one or two faults for most other
actions.

## Decisions

- **Score whose past runs an answer cites and quotes.** The report splits right answers by
  whether they cite only runs of their own fault, a run of another fault, or none, and
  counts the right answers whose quoted fixes include one of their own fault's.
- **Quote every offered run resolved by the verdict's action** (`with_past_fixes`): each
  cited run first, then the others with the same recorded action, most similar first. The
  text is still quoted from records, never generated. The API, the CLI, the demo and CI all
  show it.
- **Say what to do.** The CI comment gives the action's description from the fixed list,
  for example "correct the bench configuration", above the reason.
- **Four new faults**, each built so its evidence differs from an existing fault's, or, for
  the last, so only the version line does:
  - `analyzer_needs_alignment` (bench, `ALIGNMENT`): an admin switch makes the analyzer read
    every level high, and it logs that its alignment is out of date. The measured path loss
    falls *below* its limit, where a damaged cable pushes it above.
  - `sweep_timeout` (test, `TIMEOUT`): an admin switch delays only `INITiate`, so the
    session opens and answers, and the first sweep outlives the 2 s timeout. The server's
    delay hook now receives the line, so a delay can depend on the command.
  - `firmware_frequency_error` (firmware, `FIRMWARE`): release `0.2.0-rc3` puts the output
    4 ppm high while `FREQ?` reads back the setting: 4 kHz off at 1 GHz, past the 1 kHz
    limit, and within limits at 100 MHz.
  - `fixture_skips_reset` (test, `TEST_CLEANUP`): a test-code defect. The bench fixtures
    reset both instruments before every test, so no forgotten restore can do harm; this
    fault drops that reset, through `BENCH_DEFECT_SKIP_RESET` in
    `tests/utilities/injected_defects.py`, a hook a real suite wouldn't have. The offset
    test's offset then reaches every later test: the same failure as `reset_leaks_offset`,
    with the firmware unchanged. Its restore takes the bench down, so the next run starts
    from fresh instruments. A new bench test was the alternative; it would have changed
    every recorded run.
- **The 24 new runs were built on their own**, with `run_fault` and the same layout, so the
  54 older runs and their cached answers stayed as they were.

## Measurements

`make eval` on the 8 GB M1. The first three faults: 53 new prompts, 878 s; then
`fixture_skips_reset`: 10 new prompts, 200 s. Faults solved of 13, then confident wrong
answers of 39.

| Scenario | Faults solved | Wrong | Right: cite own / another / none | Right, quoting own fix |
|---|---|---|---|---|
| full evidence | 8 (5 to 11) | 15 | n/a | n/a |
| + retrieval + checks | 11 (8 to 12) | 3 | 29 / 4 / 0 | 33 of 33 |
| + gates (the API) | 9 (6 to 11) | 0 | 23 / 4 / 0 | 27 of 27 |
| majority vote | 12 (9 to 13) | 3 | 23 / 13 / 0 | 36 of 36 |

- On the nine older faults alone, before the quoting change, 11 of the 15 right answers
  behind the gate quoted their own fault's fix; with it, 15 of 15. The 4 that cite another
  fault are the three `config_parameter_out_of_range` questions and `link_flaky-3`.
- Of the new faults, retrieval solves `analyzer_needs_alignment`, `sweep_timeout` and
  `firmware_frequency_error`; full evidence alone solves only the firmware one, whose
  version line changed.
  Nothing solves `fixture_skips_reset`: its three identical history runs are indexed once,
  so its questions are offered one of its runs and two `config_decimal_error` runs, and
  both the model and the vote answer `CONFIGURATION`. The gate answers `UNKNOWN`, so the
  API is not wrong there; the vote is, on 3 questions.
- Older faults that changed: `slow_responses` went from 0 to 3 of 3 with retrieval, as the
  `sweep_timeout` runs made its closest past runs agree on `TIMEOUT`; `reset_leaks_offset`
  went from 0 to 3 of 3 with retrieval and checks, its look-alike now `fixture_skips_reset`
  rather than a configuration run. Behind the gate, `reset_leaks_offset` and
  `config_decimal_error` are now refused, as their closest runs disagree, so the gate costs
  6 right answers where it cost 3. Every other older fault scored as before.

## Consequences

- Quoting more runs can show two different fixes for one answer, as on
  `config_parameter_out_of_range`; each is traceable to the run that recorded it.
- Eight of the thirteen faults have no size, so their questions repeat a past run; the vote's
  12 of 13 is recall, not reasoning.
- The vote being wrong on `fixture_skips_reset` is evidence for keeping the gate in front of
  any "vote first" answer.
