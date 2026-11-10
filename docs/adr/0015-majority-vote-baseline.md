# ADR 0015: A majority-vote baseline, and what it says about the model

- **Status:** Accepted for the evaluation; whether the API should vote is still open
- **Date:** 2026-11-10

## Context

Retrieval offers the model the 3 closest past runs, and each one's label records the action
that resolved it. Scored per fault, retrieval took the model from 5 to 6 of 9 (ADR 0014).
Nothing measured whether the model reads the evidence or just copies the precedents. Copying
needs no model.

## Decision

- **`triage/vote.py` answers by majority vote:** the action that resolved strictly more of
  the precedents than any other wins. A tie, or no precedent with a recorded action, is
  `UNKNOWN`. A recorded action that isn't on the fix list is a broken data set and raises
  an error.
- **The vote goes through the same gates and checks as the model** (`vote_run`).
- **Each of the five vote scenarios is the twin of a model scenario** (`adds_to` names it),
  and differs only in who answers. `make eval` scores them every run, at no model cost.
- **The API still asks the model.** Whether it should vote first is a product call. The
  numbers below are the case for it.

## Measurements

`make eval`, 0 new prompts (every model answer came from the cache, the same answers as
ADR 0014). Faults solved of 9, with the 95% interval, then confident wrong answers of 27.

| Scenario | Model | Vote |
|---|---|---|
| retrieval | 6 (3–8), 6 wrong, 3 malformed | **9 (6–9), 0 wrong** |
| retrieval + checks | 6 (3–8), 3 wrong, 3 malformed | **9 (6–9), 0 wrong** |
| + checks + gates | 5 (2–7), 0 wrong, 3 malformed | **6 (3–8), 0 wrong** |
| own fault held out, + checks | 4 (2–7), 11 wrong | 3 (1–6), 8 wrong |
| own fault held out, + checks + gates | 1 (0–4), 3 wrong | 1 (0–4), 3 wrong |

Compared question by question:

- **Without gates, the model differs from the vote on 9 of 27 questions, and is worse on
  all 9.** 3 are its malformed `link_down` replies. The other 6 are `reset_leaks_offset` and
  `slow_responses`: 2 of their 3 precedents are the right fault, and the model followed the
  odd one out each time.
- **With the gates, the model never departs from the precedents.** It differs on the 3
  malformed replies only. Held out with the gates, all 27 answers are identical.
- **The model's one gain is held out, without the gates.** On `config_decimal_error` it
  answers `CONFIGURATION` where the precedents tie, which is right. On
  `output_command_change` it answers `LINK` where they tie, which is wrong.

## What this means, and its limits

- **On this data set, retrieval does the work.** Every question's top 3 includes a run of
  its own fault, and 6 of the 9 faults have no size to vary, so their questions repeat a
  past run almost word for word. A vote that solves 9 of 9 here shows recall. It does not
  show the problem is solved.
- **The model earns its place only where the precedents run out:** a new fault, or a tie.
  That is one fault gained and one lost, and with 9 faults one fault is within noise.
- **"Vote first, ask the model only on a tie or with no precedent" is the obvious
  candidate.** It hasn't been measured as its own scenario, so its score is unknown.
  Combining the table's rows by hand would be a guess.
- **One seed, one machine.** The multi-seed run on the roadmap would show how much of the
  model's column is noise. The vote's column doesn't depend on the seed.
