# ADR 0013: Abstain from retrieval, lead with the instrument error, ablate the firmware line

- **Status:** Accepted
- **Date:** 2026-11-09
- **Amends:** ADR 0012

## Context

ADR 0012's measurements left three questions open:

- **With no precedent of its own fault, the model never says `UNKNOWN`.** It copies the
  closest precedent's action instead. Retrieval already knows when that is likely: the
  closest past runs were resolved in different ways, or none of them is close.
- **A rejected command reads as a timeout.** `output_command_change` fails with -113
  "Undefined header", but the first failure is the timeout on the query after it. The error
  appeared only at the end of the reason line and in the logs. With its own fault held out,
  the model chose `TIMEOUT` on all three questions.
- **`FIRMWARE` is the most common wrong answer with full evidence** (9 of 14). One suspect
  is the line "Firmware: … (unchanged since the last passing run)", which names firmware
  even when it rules it out.

## Decisions

- **Two gates on the precedents, before the model is asked** (`triage/abstain.py`).
  `PrecedentsDisagree` answers `UNKNOWN` when two of the offered precedents record different
  actions; a precedent with no recorded action counts as neither agreeing nor disagreeing.
  `WeakPrecedent` answers `UNKNOWN` when the closest precedent's cosine similarity is below
  0.95; a retriever that doesn't score its sources leaves the gate silent, because unknown
  is not low. Like the checks, a gate only ever answers `UNKNOWN`.
- **The threshold comes from the history alone** (`tools/calibrate_abstention.py`). Each
  history run is asked as a question would be, from its evidence alone, against the other
  history runs as stored. The rule, fixed before the numbers were seen: the lowest
  similarity to a same-fault run, rounded down to two decimals. The question runs play no
  part, so the scenario that scores the threshold isn't the data it was tuned on.
- **An instrument error leads the full evidence:** `Instrument error: -113,"Undefined
  header", reported by 17 of 17 failed tests.` It states the error and nothing more; an
  explanation of why it matters would be a second change in the same measurement. The
  error-only evidence is unchanged: it stays what a person would paste.
- **An ablation level, `full-without-firmware`**, is the full evidence minus its firmware
  line. The evaluation scores it; the API and `make triage` don't offer it.
- **Three new scenarios**, each changing one thing in a named neighbour: full evidence
  without the firmware line; full + retrieval + checks + gates; and the same with the
  question's own fault held out.

## Measurements

`qwen2.5:3b`, temperature 0, seed 42, on the 8 GB M1, with each identical history run
indexed once (ADR 0012). All eight scenarios are in `triage/results/rag.md`; the three
without retrieval are also in `triage/results/baseline.md`. The gates ask the model nothing
new: a gated question isn't asked, and an ungated one asks what the scenario without gates
asked.

| Scenario | Faults solved (95% interval) | Questions right | `UNKNOWN` | Wrong | Malformed |
|---|---|---|---|---|---|
| full evidence | 4 of 9 (2 to 7) | 13 of 27 | 0 | 14 | 0 |
| full evidence, no firmware line | 4 of 9 (2 to 7) | 13 of 27 | 0 | 14 | 0 |
| full + retrieval + checks | 5 of 9 (2 to 7) | 15 of 27 | 6 | 3 | 3 |
| + gates | 5 of 9 (2 to 7) | 15 of 27 | 9 | 0 | 3 |
| full + retrieval + checks, own fault held out | 3 of 9 (1 to 6) | 12 of 27 | 5 | 10 | 0 |
| + gates | 1 of 9 (0 to 4) | 5 of 27 | 19 | 3 | 0 |

The first measurement of these scenarios ran on a history that indexed identical runs twice
and reported no gate firing where a precedent existed. These numbers replace it.

- **The two gates behave differently, and only one earns its place.** Across both
  retrieval scenarios:

  | Gate | Fired | Wrong answers prevented | Right answers given up | Already `UNKNOWN` |
  |---|---|---|---|---|
  | `PrecedentsDisagree` | 16 | 7 | 1 | 8 |
  | `WeakPrecedent` (held out only) | 12 | 3 | 6 | 3 |

  `PrecedentsDisagree` fires on the look-alike case: two precedents of the question's own
  fault and one of another (`reset_leaks_offset` beside `config_decimal_error`;
  `output_command_change`, held out, beside `slow_responses` and `link_flaky`). The model
  follows the odd one out, and the gate stops it. Its one cost was `config_decimal_error-2`
  held out. `WeakPrecedent` refused `config_parameter_missing` and
  `config_parameter_out_of_range`, which the model had solved from another configuration
  fault's precedents, to stop three wrong answers on `reset_leaks_offset`.
- **With a precedent of the question's own fault, the gates took wrong answers from 3 to 0
  and gave up nothing.** Held out, they took faults solved from 3 to 1, all of that from
  `WeakPrecedent`: the one right answer `PrecedentsDisagree` gave up was on a fault already
  unsolved (`config_decimal_error`, 1 of 3).
- **Leading with the instrument error changed no score.** `output_command_change` was
  already solved with full evidence (`FIRMWARE`, 3 of 3). With its own fault held out, the
  model followed a `link_flaky` precedent to `LINK` despite the -113 line leading the
  evidence.
- **The firmware line cuts both ways, and nets to nothing.** Without it,
  `config_decimal_error` goes from `FIRMWARE` (wrong) to `CONFIGURATION` (right), and
  `reset_leaks_offset` from `FIRMWARE` (right) to `CONFIGURATION` (wrong). Wrong `FIRMWARE`
  answers fall from 9 to 6. So the line does pull the model to `FIRMWARE` when it rules
  firmware out, and is also the evidence that solves the one fault where firmware changed
  and nothing else says so.

## Why no similarity threshold would have worked here

With each question's own fault held out, the closest precedents scored:

| Question | Closest precedents (action) | Similarity | Model's answer without gates |
|---|---|---|---|
| `config_parameter_missing-1` | `config_parameter_out_of_range` (`CONFIGURATION`) | 0.934 | right |
| `config_parameter_out_of_range-1` | `config_parameter_missing` (`CONFIGURATION`) | 0.938 to 0.939 | right |
| `reset_leaks_offset-1` | `config_decimal_error` (`CONFIGURATION`) | 0.946 to 0.947 | wrong |
| `cable_loss-1` | `link_down`, `link_flaky` (`LINK`) | 0.958 to 0.970 | wrong |

The precedent that misled the model is closer than the ones that helped it, so any threshold
that refuses `reset_leaks_offset` also refuses both configuration faults. Calibrating on the
closest same-action run instead of the same fault doesn't change that order. Similarity
measures how alike two failures look, and here a firmware regression looks like a
configuration error. `cable_loss` shows the other limit: three agreeing, close precedents,
all wrong, which neither gate can catch.

## Consequences

- The gates run in the evaluation only, and the API doesn't apply them yet.
  `PrecedentsDisagree` prevented 7 wrong answers for 1 right one and is the candidate for
  the API, on its own. `WeakPrecedent` costs more right answers than it saves wrong ones,
  and on this data no threshold would do better; it stays in the evaluation as a record.
- The instrument-error line stays: it changed no score, but it puts the cause before its
  consequence for a person reading the evidence, at the cost of one line.
- The firmware line stays: removing it loses `reset_leaks_offset`. What to try instead is a
  wording that doesn't name firmware when it is unchanged.
- On real benches, the same-fault and other-fault similarities will overlap more than on
  27 simulated runs. `tools/calibrate_abstention.py` recalibrates the threshold from the
  history, but the table above says a recalibrated threshold is not enough on its own.
