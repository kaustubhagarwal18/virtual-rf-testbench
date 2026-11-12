# ADR 0022: Checks are rules in the domain pack, over domain-free fact kinds

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

ADR 0021 moved the actions into a pack file. The five checks stayed as Python classes that
named RF actions and RF facts: the `firmware_versions` field, release `0.1.0`, the phrase
"No signal reaching the analyzer", the units dB and dBm. A second domain would have needed
new classes in the core. The pipeline also read the default pack everywhere, so no other
pack could be used even with its own actions.

## Decisions

- **Fact kinds in the core** (`triage/facts.py`): five shapes that recur across domains.
  A version field equals a baseline; the answer names a version the run didn't record; a
  failed test lists errors; a log line contains a phrase; a reading in some units is out of
  limits. Each returns None when it doesn't hold, or the values a reason may quote.
- **Facts and checks in the pack.** `[facts.<name>]` gives a kind its field names,
  phrases and units; `[[checks]]` refuses an action `when` a fact holds or `unless` it
  does, with a reason. A check still never picks another action. Loading refuses a rule
  that refuses an unknown action or `UNKNOWN`, names no fact or two, names an unknown
  fact, quotes a value its fact can't give (an `unless` rule can quote none), or repeats a
  name; and a fact of an unknown kind or with wrong parameters.
- **One check class applies every rule** (`RuleCheck`); the protocol stays, so a check too
  subtle for a rule can still be code.
- **The pack travels through the pipeline:** the prompt, the answer schema, parsing,
  `triage_run`, the vote and CI's triage take a pack, defaulting to the RF bench.
  `python -m triage.service` and `python -m tools.ci_triage` take `--pack`. Another pack
  always sends its own schema, since the provider's built-in one is the RF pack's.

## Consequences

- The behaviour lock was first widened so every check fires: each action is checked with
  answers naming no release, a release no run ran, and releases some runs ran, which gives
  213 refusals across all five checks over the 39 questions. It was recorded before the
  move and passes unchanged after it, so every refusal reads word for word as before.
- A payments pack with its own facts and checks runs through the prompt, parsing, the vote
  and the checks in the tests, with no RF action or word in its prompt.
- Three RF values now sit in both the pack and the evidence builder (the baseline release,
  the "No signal" phrase, the level units); a test keeps them equal until the evidence
  moves into the pack with the case schema.
- The API and `make eval` still use the default pack.
