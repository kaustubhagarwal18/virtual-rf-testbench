# How the triage assistant is measured

The README says what the assistant does. This page is for the numbers behind it: how each
answer is kept from being confidently wrong, how it scores on the labelled faults, and how
the evaluation was made harder to fool. `make eval` reproduces every number here; each
question's answer is in [`triage/results/rag.md`](../triage/results/rag.md).

The data set is 27 failed runs of 9 known faults, three per fault, scored per fault with a
95% interval ([`triage/data/README.md`](../triage/data/README.md) describes it). The answering
model is `qwen2.5:3b` on local Ollama, at temperature 0 with a fixed seed, on one machine.

## Guardrails against a confident wrong answer

A wrong action costs an engineer hours; `UNKNOWN` costs a minute. Every layer below can only
turn an answer into `UNKNOWN` or refuse it, never pick a different action.

| Layer | What it does | Measured effect |
|---|---|---|
| **Fixed action list** | the model picks a key, so scoring is an exact match | no judge model needed |
| **Reply schema** | one action from the enum; `cited` limited to the offered ids; 512-token cap | an invented citation can't be written |
| **Strict parser** | anything unusable is counted as malformed, never read generously | none in the current run; 3 `link_down` replies before ADR 0017 |
| **Disagreement gate** | top 3 past runs resolved different ways → `UNKNOWN`, model not asked | wrong answers 3 → 0, at a cost of 3 right ones |
| **Checks** | `FIRMWARE` on a release the run didn't run, or on unchanged firmware; `TIMEOUT` beside an SCPI error; `ALIGNMENT` when the analyzer logged no signal | turned 6 wrong answers into `UNKNOWN`, cost none |

## Results

<p align="center">
  <img src="images/triage-results.svg" width="860" alt="Faults solved out of 9: error only 3, full evidence 7, without the version line 6, without the no-signal line 5; with retrieval 6, with checks 6, with the disagreement gate 5 and no wrong answers; a majority vote over the same past runs, with no model, 9 with no wrong answers, and 6 with the gate; with the question's own fault held out, 4 with checks (the vote 4) and 1 with both gates (the vote 2).">
</p>

Counts, not percentages: with 9 faults, "6 of 9, 3 to 8" is honest where "67%" is not.
Every question's answer is in [`triage/results/rag.md`](../triage/results/rag.md).

## What the numbers say

- **Counting the past runs' actions beats the model on this data set.** A majority vote over
  the same top 3, with no model, solves 9 of 9 with no wrong answers, against the model's 6.
  Behind the gate the model differs from the vote only on `link_down`: it answers
  `UNKNOWN` where the vote says `LINK`. This is recall: every question had a past run of its
  own fault.
  The model's one gain is a new fault where the precedents tie (`config_decimal_error`);
  held out, it also loses `link_flaky`, which the vote gets right, so both solve 4 of 9.
- **Past runs help the model on one fault and mislead it on two.** The run's own evidence
  solves 7 of 9; with the past runs, 6. They fix `config_parameter_out_of_range`, but the
  model copies "recalibrated" from `link_down`'s past fixes into `ALIGNMENT`, and
  `reset_leaks_offset` becomes `CONFIGURATION`. With the fault held out, 4 of 9.
- **Stating a fact from the logs can matter.** A line saying the analyzer logged "No signal
  reaching the analyzer" takes the evidence-only answer from 5 to 7 of 9: the ablation
  without it loses `link_down` and two of three `link_flaky` questions (ADR 0017).
- **A small model follows the odd precedent out.** Offered two past runs of its own fault
  and one look-alike, it often copies the look-alike. That is why the gate exists.
- **Wording matters.** "Firmware: … (unchanged)" pulled the model to `FIRMWARE`. Saying
  "Instruments: … the same as the last passing run" solved one more fault and lost none.
- **Four of the six solved faults are recall, not reasoning:** their runs have no size to
  vary, so a question repeats a past run almost word for word. The report says so.
- **Two ideas didn't pay off:** leading the evidence with the SCPI error changed no score,
  and a similarity threshold gave up 6 right answers to prevent 3 wrong ones.

## How the evaluation got more honest

Each review found a way the score could flatter the assistant, and closed it.

| ADR | What changed | Retrieval score after it |
|---|---|---|
| [0008](adr/0008-triage-design.md) | fixed action list, one prompt for every scenario | 6 of 9 questions |
| [0009](adr/0009-honest-triage-evaluation.md) | neutral source ids (the folder name gave the answer), 27 questions, a held-out scenario | 18 of 27 questions |
| [0012](adr/0012-score-faults-key-labels-by-action.md) | score faults, not questions; label each run's action; **index identical runs once** (they inflated 5 → 8 of 9) | 5 of 9 faults |
| [0013](adr/0013-abstain-from-retrieval.md) | gates before the model; an ablation of the firmware line | 5 of 9 faults |
| [0014](adr/0014-api-gate-cited-ids-version-wording.md) | the disagreement gate in the API, cited ids in the schema, version wording | **6 of 9 faults** |
| [0015](adr/0015-majority-vote-baseline.md) | a majority vote over the same past runs, with no model, as a baseline | 6 of 9; the vote 9 of 9 |
| [0017](adr/0017-no-signal-line-and-alignment-check.md) | a "No signal" line in the evidence; `ALIGNMENT` without a signal becomes `UNKNOWN` | 6 of 9; without past runs 7 of 9 |
