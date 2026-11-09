# ADR 0009: Measure triage so the score can't flatter it

- **Status:** Accepted
- **Date:** 2026-11-09
- **Amends:** ADR 0008

## Context

ADR 0008 reported 6 of 9 for full evidence plus retrieval. Reviewing that number turned up
four ways it could mislead:

- **The answer was in the source id.** A history source was cited by its folder name, so
  the model saw `[cable_loss-1]` beside the evidence.
- **Every question had a precedent of its own fault.** The history holds three runs of every
  fault, so the scenario measured whether the model copies a near-identical past fix. It
  didn't say what happens when a failure is new.
- **Nine questions are too few.** One answer moved the score by 11 points, and "6 of 9" came
  with no sense of how much it could move.
- **One number hid what kind of miss it was.** `UNKNOWN`, a confident wrong action and an
  unusable reply all counted the same, though the prompt asks for `UNKNOWN` precisely because
  a confident wrong action is the expensive one.

## Decisions

- **Neutral source ids.** A source is cited by a hash of its text (`past-3fa2c1d0`). Its
  labelled fault rides on the `Source` for scoring and filtering, never in a prompt.
- **Three question runs per fault**, 27 in all, at sizes no history run has where the fault
  has a size.
- **Five scenarios, each adding one thing to a named neighbour:** error only; full
  evidence; full + retrieval; full + retrieval + firmware check; and full + retrieval with
  the question's own fault held out of the history.
- **Four outcomes, counted apart:** right, `UNKNOWN`, confidently wrong and malformed. Each
  score carries a 95% Wilson interval. Retrieval recall (was a past run of the question's own
  fault offered?) is reported separately from answer accuracy.
- **A firmware check.** A `FIRMWARE` verdict becomes `UNKNOWN` when the firmware is known to
  be unchanged since the last passing run. It never substitutes another action, and it leaves
  the verdict alone when the versions weren't recorded.
- **Schema-constrained replies.** The model is asked through Ollama's structured outputs,
  with the actions as an enum. Replies are still parsed strictly.
- **Each distinct prompt is asked once.** The checked scenario changes nothing before the
  model answers, so it reuses the retrieval scenario's replies and costs no calls.

## Measurements

`qwen2.5:3b`, temperature 0, seed 42, on the 8 GB M1, over 27 questions; `make eval` took 23
minutes. All five scenarios are in `triage/results/rag.md`.

| Scenario | Right (95% interval) | `UNKNOWN` | Wrong | Malformed |
|---|---|---|---|---|
| error only | 12 of 27 (7 to 17) | 0 | 15 | 0 |
| full evidence | 9 of 27 (5 to 14) | 0 | 18 | 0 |
| full + retrieval | 18 of 27 (13 to 22) | 0 | 9 | 0 |
| full + retrieval + firmware check | 18 of 27 (13 to 22) | 3 | 6 | 0 |
| full + retrieval, own fault held out | 9 of 27 (5 to 14) | 0 | 18 | 0 |

- **The id leak didn't change a single answer.** The retrieval scenario was run once more
  with the old folder-name ids. It chose exactly the same action on all 27 questions (18 of
  27), so the ids weren't what lifted the score; each source's text already states its fix.
  They are neutral now anyway, because a measurement shouldn't depend on that luck.
- **Retrieval works by finding the same fault.** It offered a past run of the question's own
  fault for 27 of 27 questions, and lifted the score from 9 to 18. With the fault held out,
  the score fell back to 9, no better than no retrieval at all.
- **Held out means no precedent of the fault, not of the fix.** 12 of the 27 held-out
  questions were still offered a sibling fault with the same fix (`link_down` for
  `link_flaky`, one configuration fault for another). The six configuration questions among
  them were right; the six link questions were not. With no same-fix precedent, 3 of 15 were
  right (all `cable_loss`).
- **The model never abstains.** It said `UNKNOWN` 0 times in its 108 answers (the four
  scenarios that ask it; the checked one reuses replies), held-out scenario included,
  despite the instruction. Every miss was a confident wrong action until the
  firmware check turned 3 of them (the three `slow_responses` questions) into `UNKNOWN`.
- **The check helps where it should, and only there.** It removed 3 confident wrong answers
  and cost no right ones: the two firmware faults run a changed release, so it never touches
  them.
- **The schema ended malformed replies:** 0 of 108, against 1 of 27 in ADR 0008's
  measurement.
- **The link faults are never solved with retrieval.** Each of the 6 link questions cited a
  LINK precedent and still chose `CABLE`. The `link_down` history's recorded fix reads
  "Reconnected the cable between the instruments", which may be what pulls it to `CABLE`.
  That is untested.
- More evidence still lowers the score without retrieval (12 to 9), as in ADR 0008.

## Consequences

- The headline is no longer "6 of 9" but "18 of 27 (13 to 22) when the same fault has been
  seen, 9 of 27 when it hasn't". Retrieval is a lookup of past fixes; it doesn't help the
  model reason about a new failure.
- The next lever for new failures is getting the model to abstain, through more checks like
  the firmware one or a stronger model. Wording the link label's fix without "cable" is a
  cheap experiment worth running.
- The questions of the faults with nothing to scale repeat one scenario, so the interval,
  which assumes independent questions, is if anything too narrow.
