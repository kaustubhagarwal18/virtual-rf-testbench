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
- **One block per failure, each saying what to do.** Each failure gets a numbered heading
  naming it ("Failure 2 of 4: `test_level_accuracy_across_levels: Failed`"), the tests in it,
  the answer and its cause, and a "What to do" line: the action's description, or, for
  `UNKNOWN` and an unusable reply, to look at the failure by hand. Two failures with the same
  answer still get two blocks: merging them hid which failure was which, and left an
  `UNKNOWN` block with nothing to do. An answer a check overruled shows no "Last time"
  line: those are fixes for the action the check refused, and quoting them beside "look at
  this failure by hand" read as advice to apply them. A reply that can't be used is that failure's problem;
  the other failures keep their answers.
- **A fifth check, and one more line in a focused run's evidence.** `ALIGNMENT` becomes
  `UNKNOWN` when no level or power reading (dB, dBm) is out of limits, since an alignment
  error shifts levels, not frequencies. A focused failure whose failed readings are all
  frequencies or relative levels says "No level or power reading is out of limits in this
  failure", so another failure's warning in the shared logs reads as not its cause. The
  line is added to focused runs only, so the history and the evaluation keep their
  evidence. In the 39 recorded question runs, no `ALIGNMENT` answer was on a run without a
  failed level reading, so no recorded result changes. Whether the line moves the model to
  `FIRMWARE` is measured in CI, not assumed.
- **Test names are untrusted.** The pull request's code chooses them, so they are shown as
  inline code like the model's words (ADR 0018).

## Consequences

- In CI, with both faults again (run 43006883624), the 8 failed tests formed 4 failures:
  path loss answered `ALIGNMENT` and the first frequency failure `FIRMWARE`, both right and
  both quoting their own fault's past fix. The model answered `ALIGNMENT` on the other two
  frequency failures, and the new check turned both into `UNKNOWN`. The README shows the
  comment.

- The HTTP API still returns one verdict, for the first failure. A per-failure answer there
  is planned.
- The evaluation still scores one answer per question run. Scoring every failure against its
  run's label is not done yet: it needs about 116 new prompts.
- A focused run keeps the whole run's instrument logs, because a log line names no test.
  A "No signal" logged during one failure is seen by every failure's checks, and can turn
  another failure's `ALIGNMENT` answer into `UNKNOWN`. Worse, a warning can pull another
  failure to the wrong action. In CI, a run with two unrelated faults (release `0.2.0-rc3`,
  frequency 4 ppm high, and a 3 dB alignment error) split into 4 failures. Path loss was
  answered `ALIGNMENT`, rightly. Two of the three frequency failures were also answered
  `ALIGNMENT`, pulled by the analyzer's alignment warning, and the third `UNKNOWN` by the
  gate. None got `FIRMWARE`.
- CI triages bench failures only; unit and integration failures are listed in the comment
  but not explained.
- The time the job takes on a GitHub runner with several failures is measured in CI, not
  estimated here.
