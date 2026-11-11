# ADR 0020: In CI, triage each distinct failure on its own, at most five per run

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

CI triaged a failed run by its first failure only (ADR 0018). A run can fail in more than
one way, and a reviewer reading the comment wants to know what to do about each failed test,
not only the first. Asking once per failed test would be wasteful: in the 39 recorded
question runs, 116 distinct failures account for 415 failed tests, and 24 runs
have one distinct failure, such as 19 tests stopped by the same setup step. Each answer is
one model prompt, which takes 20 to 60 s on a CPU runner.

## Decisions

- **Group the failed tests** (`triage/failures.py`). The tests a failed setup step stopped
  are one failure. The other tests are grouped by test function and kind of failure (the
  exception's class, or `Failed`), so the parametrized cases of one test that fail the same
  way are asked about once. On the recorded runs this gives 1 failure in 24 runs, 3 in 6,
  and 4 to 11 in the other 9: 116 in all.
- **Focus the run on each group** and triage the focused copy with the API's pipeline:
  retrieval, the disagreement gate, the model, the checks. The focused run keeps the passed
  tests, the logs, the versions and the totals. Its evidence says how many tests failed this
  way and names the run's other failures, in place of "N further tests failed after it".
  A whole run's evidence is unchanged, so the history, the API and the evaluation see what
  they saw before.
- **At most five failures per run** (`--max-failures`, default 5): at most about five
  minutes of prompts, within the job's 20-minute timeout. The others are listed as not
  triaged, with their test counts, rather than dropped.
- **One block per distinct answer.** Failures that get the same action, and the same gate
  or check reason if one stood in for the model, share a block that lists all their tests.
  The first failure's reason and past fixes are shown. A reply that can't be used is that
  failure's problem; the other failures keep their answers.
- **Test names are untrusted.** The pull request's code chooses them, so they are shown as
  inline code like the model's words (ADR 0018).

## Consequences

- The HTTP API still returns one verdict, for the first failure. A per-failure answer there
  is planned.
- The evaluation still scores one answer per question run. Scoring every failure against its
  run's label is not done yet: it needs about 116 new prompts.
- The time the job takes on a GitHub runner with several failures is measured in CI, not
  estimated here.
