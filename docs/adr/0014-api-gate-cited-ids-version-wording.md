# ADR 0014: The disagreement gate in the API, cited ids in the schema, and version wording

- **Status:** Accepted
- **Date:** 2026-11-09
- **Amends:** ADR 0013

## Context

ADR 0013 left three follow-ups:

- `PrecedentsDisagree` prevented 7 wrong answers for 1 right one, but only the evaluation
  applied it.
- With retrieval, every `link_down` reply was malformed: the model put a source's text in
  `cited` instead of its id.
- "Firmware: … (unchanged since the last passing run)" pulled the model to `FIRMWARE` on
  `config_decimal_error`, yet removing the line lost `reset_leaks_offset`.

## Decisions

- **The API applies `PrecedentsDisagree`, and only it** (`SERVICE_GATES`). A gated reply
  says why in `gate_reason`. `create_app` takes the gates as a parameter.
- **Each question's schema lists the ids it was offered.** `LLMProvider.complete` takes an
  optional per-reply schema; with sources, `cited` is an enum of their ids. Without sources
  the general schema is used, so those cached answers keep their keys.
- **Replies are capped at 512 tokens.** Constraining `cited` moved `link_down`'s failure:
  the model wrote the source's text into `because` instead and never closed the string, so
  the request ran until Ollama's 5-minute timeout and stopped the evaluation. Capped, the
  reply is cut off and counted as malformed. The longest stored answer was about 350
  tokens, so the cap changed none of them; it is part of the provider's identity, and the
  cached answers were re-keyed to include it.
- **Unchanged versions don't say "firmware".** They read "Instruments: analyzer 0.1.0,
  generator 0.1.0, the same as the last passing run." Changed and unrecorded firmware keep
  their "Firmware:" wording, because there it is the evidence.

## Measurements

`qwen2.5:3b`, temperature 0, seed 42, on the 8 GB M1; 34 new prompts. All eight scenarios
are in `triage/results/rag.md`.

| Scenario | Before (ADR 0013) | After |
|---|---|---|
| full evidence | 4 of 9 faults, 14 wrong | **5 of 9**, 11 wrong |
| full evidence, no version line | 4 of 9, 14 wrong | 4 of 9, 14 wrong |
| full + retrieval | 5 of 9, 9 wrong, 3 malformed | **6 of 9**, 6 wrong, 3 malformed |
| + checks | 5 of 9, 3 wrong | 6 of 9, 3 wrong |
| + checks + disagreement gate (the API) | 5 of 9, 0 wrong | 5 of 9, 0 wrong |
| + checks, own fault held out | 3 of 9, 10 wrong | 4 of 9, 11 wrong |
| + checks + both gates, own fault held out | 1 of 9, 3 wrong | 1 of 9, 3 wrong |

- **The wording solved `config_decimal_error` and kept `reset_leaks_offset`**, in full
  evidence and with retrieval: the gain the ablation pointed to, without its loss.
- **`link_down` is still 3 malformed with retrieval**, now cut off by the cap rather than
  citing text. The schema fixed the form of the failure, not the judgement behind it: the
  model writes `ALIGNMENT` with "LINK" as the place to act, then rambles.
- **The disagreement gate now costs right answers.** Across both retrieval scenarios it
  fired 17 times: 8 wrong answers prevented, 6 right ones given up, 3 already `UNKNOWN`.
  The cost is `config_decimal_error`, which the model now gets right despite a
  disagreeing `reset_leaks_offset` precedent. In the API's own case, with a precedent
  available, it takes wrong answers from 3 to 0 and right answers from 18 to 15.

## Consequences

- The API keeps the gate. A confident wrong action costs an engineer hours and an `UNKNOWN`
  costs a minute, so trading 3 right answers for 3 wrong ones is worth it here; the
  trade is stated in the README rather than hidden.
- If a better prompt makes the model resist look-alike precedents more often, the gate will
  cost more; `make eval`'s gated scenarios measure that each time.
- `link_down` needs a different fix: the model's ramble starts before any citation, so a
  schema can only cut it short.
