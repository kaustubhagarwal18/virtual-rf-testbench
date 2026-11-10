# ADR 0016: The answer states the cause and quotes what fixed it last time

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

The project's question is "is this the firmware, the bench or the test, and what fixed it
last time?" The answer gave one action from a fixed list, where to act, why, and the ids of
the past runs it cited. The cause was only implied by the action. The fix from last time
was in the cited runs' records, so an engineer had to look the ids up to read it.

## Decision

- **The cause is looked up, not asked for.** Each action maps to one cause: `CABLE`, `LINK`
  and `ALIGNMENT` to the bench; `CONFIGURATION`, `TIMEOUT` and `TEST_CLEANUP` to the test;
  `FIRMWARE` to the firmware; `UNKNOWN` to unknown (`fix_category` in `triage/prompt.py`). A
  test checks the mapping against every fault's label, so the two can't drift apart.
- **The past fix is quoted, not written by the model.** A history source keeps its label's
  `fix` text. After the gates and checks, the verdict gets the recorded fix of each run it
  cited, in citation order (`with_past_fixes`). A cited run with no recorded fix is left out
  rather than shown as blank. When several cited runs record the same fix, it is printed
  once with all their ids.
- **The model's prompt and reply schema don't change.** Asking the model to repeat the fix
  or name the cause would add two more things it could get wrong. Both are facts the
  pipeline already holds.

## Consequences

- `make triage`, `make triage-demo` and the API's `POST /triage` (`category`,
  `past_fixes`) all show both. On `config_decimal_error-1`, the demo's retrieval answer now
  reads "cause: test" and "last time: Corrected level_offset_db to 1.5 (past-9e31db17,
  past-4b20146a)": each fix beside the cited runs that recorded it.
- No stored answer changed: `make eval` asks 0 new prompts and `triage/results/rag.md` is
  unchanged. The score still counts the action only.
- `make triage` offers no past runs, so it shows the cause but never a past fix.
- When a check overrules the model to `UNKNOWN`, the past fixes of the runs it cited are
  still shown. They are context from the record, not a recommendation.
- Not done: scoring whether `where` names the right thing. It is the part of the answer the
  model writes freely, and nothing measures it yet.
