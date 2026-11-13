# ADR 0024 — Rules-only baseline, checks-without-retrieval scenario, and eval-ceiling CI

## Context

After ADR 0019 expanded the data set to 13 faults and added a majority-vote baseline, three
gaps remained in the evaluation:

1. **No controlled floor.** Every scenario gave the model at least the run's evidence. There
   was no way to confirm that a scenario scoring 0 of 13 was actually working as designed
   rather than broken silently.

2. **Checks and retrieval were always measured together.** The "full + retrieval + checks"
   scenario showed the checks' effect only over the retrieval baseline, not over evidence
   alone. It was unclear whether checks helped without retrieval, or only when retrieval had
   already narrowed the answer.

3. **The evaluation ran only on `qwen2.5:3b`.** Results on a single model on a single machine
   can't distinguish a data-set ceiling from a model-capacity limit, and the evaluation never
   ran in CI, so a prompt or pipeline change could silently regress the numbers.

Three case-loader bugs were also found during this work: a partial selection in `case.json`
crashed evidence building with a `KeyError`; skipped items were counted as failures, stealing
the first-failure slot; and misspelt outcomes like `PASSED` or `passeed` counted silently as
failures instead of raising an error.

## Decision

**Rules-only baseline.** A new scenario applies only the five rule checks, with no evidence
passed to the model and no model call made. Every question returns `UNKNOWN` by design. This
is the controlled floor: a scenario that scores below it has regressed. The result — 0 of 13
faults, 0 of 39 questions, 0 wrong answers on both models — confirms the checks behave
correctly when given nothing.

**Full + checks scenario.** A new scenario applies full evidence plus the five rule checks,
with no retrieval. This isolates the checks' effect over the evidence-only baseline without
the confound of retrieval. Result: 8 of 13 (same faults as full evidence alone), but with
fewer confident wrong answers — several are converted to `UNKNOWN` by the check that refuses
`TIMEOUT` beside a confirmed SCPI error. No new faults are solved because the checks can only
withhold a verdict, never supply the right one.

**Eval-ceiling CI.** Two parallel jobs run the full evaluation against `qwen2.5:3b` and
`qwen2.5:7b` on each pull request labelled `eval-ceiling`, uploading the report as an
artifact. The workflow validates the model tag and the bench-results path before running.
Running both models in CI surfaces the ceiling question: does a larger model raise it?

**Case-loader fixes.** The three bugs are fixed and parametrised regression tests cover each
one, including the value boundaries a type allows.

## Consequences

**7b matches 3b at every retrieval scenario.** Both models solve 11 of 13 with retrieval and
checks, and 12 of 13 by majority vote. The 7b model scores slightly higher without retrieval
on some scenarios (6 of 13 at error-only vs 5) but not consistently. The ceiling at 12 of 13
is a data-set limit, not a model-capacity limit: eight of thirteen faults have no variation
between their history and question runs, so the question is recall, not reasoning.

**Checks without retrieval reduce wrong answers, not the fault count.** Full + checks solves
the same 8 of 13 as full evidence, but `output_command_change` moves from 3 wrong `TIMEOUT`
answers to 3 `UNKNOWN`. The checks are still worthwhile here — a wrong confident answer costs
an engineer more than an UNKNOWN — but their main value remains in combination with
retrieval, where they turn the remaining 3 wrong answers to 0 behind the gate.

**The evaluation now has a floor and runs in CI.** A regression in a prompt, a check, or the
case loader will appear in the next labelled pull request rather than going unnoticed until a
local run.

**Known limit.** The eval-ceiling jobs run Ollama on the CI runner with model weights pulled
fresh each run. `qwen2.5:7b` takes roughly three hours; a future optimisation could cache the
weights between runs.
