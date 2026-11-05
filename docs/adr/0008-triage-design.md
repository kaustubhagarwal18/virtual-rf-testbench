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

- **Retrieve with an in-memory cosine index for now.** The labelled history runs are embedded
  (nomic-embed-text, local under Ollama) and searched by cosine similarity in process, behind
  a small `Retriever` interface. At the data set's current size this needs no new
  infrastructure. A pgvector-backed retriever is a later option behind the same interface —
  the service can't tell which it was given (Liskov) — and is deferred until the data set is
  large enough to warrant it.

## Consequences

- The baseline (no retrieval) is measurable on its own, and the retrieval scenario is measured
  against it by changing one argument, so any gain is attributable to retrieval.
- The baseline run scored 4 of 9 (error text only) and 3 of 9 (full evidence): fuller context
  did not help this model, which falls back to one category when unsure. That is the floor
  retrieval has to beat, recorded in `triage/results/baseline.md`.
- Full evidence plus the three most similar past runs scored 6 of 9 on the same questions and
  model — above the 4-of-9 floor, and reversing the "more context hurt" effect (3 to 6). It
  fixed the bench and configuration faults where a cited past run breaks the model's firmware
  default (`cable_loss`, `config_parameter_out_of_range`, `config_decimal_error`). The
  three-way comparison is in `triage/results/rag.md`.
- The three remaining misses show the ceiling is the model, not retrieval. On `link_flaky`
  and `slow_responses` the retriever cited the matching past failures — the runs labelled LINK
  and TIMEOUT — and the model still chose `CABLE` and `FIRMWARE`; the precedent was in front of
  it and it did not act on it. `link_down` returned an answer that named no action at all and
  was refused as unusable. So 6 of 9 is a limit of a 3B model's reasoning and output
  discipline on an 8 GB machine, not of what retrieval surfaced. A stronger model, run where
  there is room for it, is the lever — not a change to the retriever. The report states this
  as a computed finding, so it cannot drift from the numbers.
- Testing that lever locally is not possible on the 8 GB machine. `qwen2.5:7b-instruct` (q4,
  4.7 GB) was pulled and run through the same eval, but it offloads partly to CPU and a single
  answer exceeds the 300 s per-call timeout, so the run aborts with no result; RAM, not disk,
  is the binding limit. The eval takes a `--model` flag (`make eval MODEL=…`) so the 3B-vs-7B
  comparison can be run where there is RAM for a 7B, or against a hosted model — a follow-up,
  not something this machine can do.
- A verdict whose reasoning is sound but whose category is wrong still counts as a miss, so
  the counts, if anything, understate the model. The evaluation is a measurement, not a merge
  gate, and is not run in CI (it needs a local Ollama; surfacing the score in CI from recorded
  model outputs is a possible follow-up).
