# ADR 0008: How the triage service is built, and how it is measured

- **Status:** Accepted
- **Date:** 2026-11-05

## Context

The triage service explains a failed bench run: it reads the run's evidence and says what to
do about it. It runs a small local model (qwen2.5:3b, chosen in the Ollama spike for an 8 GB
machine). The point of the project is to measure whether this helps, so the service has to be
built in a way that makes an honest measurement possible, and the measurement has to compare
like with like.

## Decisions

- **Answer with the fix, not the category.** The spec frames triage as firmware / bench /
  test. People don't act on a category; they act on a fix. So the model chooses one action
  from a fixed list (replace the cable, raise the timeout, correct the configuration, report
  a firmware regression, ...) and `UNKNOWN` when the evidence won't support one. Scoring is
  then an exact comparison, with no second model judging wording. The three sources are still
  covered by the faults, so the firmware/bench/test split is not lost, only not the output.

- **Everything behind small interfaces, injected.** `LLMProvider` has one method; the
  pipeline depends on it, never on Ollama. That is what lets the unit tests run offline with
  a fake, and what will let the retrieval scenario reuse the same pipeline by passing sources
  instead of none. The embedding model for retrieval will get its own `Embedder` interface,
  not be forced through `LLMProvider`, because turning text into a vector is a different job.

- **One prompt and one answer schema for every scenario.** The baseline and the retrieval
  scenario differ only in whether sources are supplied. If the instructions or the schema
  differed, the measured difference would include prompt edits, not just what retrieval adds.

- **The label is never shown to the model.** `make fault` saves the cause in `label.json`,
  apart from the evidence. The evidence builder never reads it; the evaluation reads it only
  to score. A model scored against evidence it was given would prove nothing.

- **Lead the evidence with the first failure.** A run with nineteen errors has one cause and
  eighteen consequences. The evidence names the failure the rest follows from — a failed
  setup step outranks any test it caused — so the model starts where a person starts.

- **Refuse an unusable answer; never retry it.** A reply that isn't JSON, names an action off
  the list, or cites a source it wasn't given is rejected. How often that happens is a result
  worth recording, so the pipeline asks once.

- **Report counts, and compute the findings from them.** Nine questions give counts, not
  percentages. The report's conclusions are derived from the outcomes in code, so they can't
  drift from the numbers above them when the evaluation is re-run on another machine.

## Consequences

- The baseline (no retrieval) is measurable on its own, and the retrieval scenario will be
  measured against it by changing one argument, so any gain is attributable to retrieval.
- The first baseline run scored 4 of 9 (error text only) and 3 of 9 (full evidence): fuller
  context did not help this model, which falls back to one category when unsure. That is the
  floor retrieval has to beat, recorded in `triage/results/baseline.md`.
- A verdict whose reasoning is sound but whose category is wrong still counts as a miss, so
  the counts, if anything, understate the model. The evaluation is a measurement, not a merge
  gate, and is not run in CI.
