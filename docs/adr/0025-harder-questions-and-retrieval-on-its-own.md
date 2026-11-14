# ADR 0025 — Harder RF questions and retrieval scored on its own

## Context

ADR 0024 gave the evaluation a controlled floor and ran it on two models, but a 2026-11-12
review of the project made a sharper point: on this data set, counting the past runs' most
common action (majority vote, no model) already beats `qwen2.5:3b`, and 8 of 13 faults have
no size to vary between their history and question runs, so answering them is recall of a
near-identical run, not reasoning. Before building a second domain pack on this core, the
evaluation needed to be harder to fool in three ways:

1. **The ablations only removed one line.** `FULL_WITHOUT_FIRMWARE` and
   `FULL_WITHOUT_SIGNAL` each drop one sentence. Nothing measured what happens with no
   instrument logs at all, or with the setup-sequence log silent, so the "first failure"
   evidence falls back to the failed test itself — the harder, more realistic version of a
   stripped-artefact bench run.
2. **The held-out scenarios only hid the question's own fault.** A retriever that can't
   return `cable_loss` runs can still return `analyzer_needs_alignment` — a different fault
   in the same bench category, plausible as a look-alike. Nothing measured generalisation
   across a whole category of faults, the harder and more realistic version of "a genuinely
   new kind of failure."
3. **Retrieval quality was only ever measured through the model's answer.** A retrieval
   miss the model happens to paper over (or a hit it ignores) is invisible in the
   classification score. There was no Recall@K or MRR number for the retriever on its own.

## Decision

**Two new evidence ablations** (`triage/evidence.py`): `FULL_WITHOUT_LOGS` strips the
log-count line and every indented log line, keeping the SCPI errors, limits and sequence
log; `FULL_WITHOUT_SEQUENCE` builds evidence as if the run had no setup steps, so
`first_failure` falls through to the failed test instead of the step that caused it. Both
are evaluated-only, like the existing ablations, never offered to `triage.service` or the
API.

**A category-held-out precedent scenario** (`Precedents.CATEGORY_HELD_OUT` in
`triage/scores.py`): every fault sharing the question's labelled category is excluded from
retrieval, not only the question's own fault. `_sources_for` in `triage/evaluate.py` builds
the exclusion set from the fault catalogue. `Retriever.similar_to` takes
`exclude_faults: frozenset[str]` instead of a single optional fault, so both the fault- and
category-held-out scenarios share one mechanism.

**A retrieval-only benchmark** (`triage/retrieval_metrics.py`, `make retrieval-eval`):
Recall@1/3/5 and MRR against the question runs, scored by a relevance rule injected rather
than assumed (the only rule shipped, `same_fault`, counts a source relevant only when it
records the question's own fault). It runs three scenarios — all history, own fault held
out, category held out — splits results into size-varied and repeated families (the
heuristic ADR 0012 already uses, following the label's `unit` field), and for the held-out
scenarios reports what was retrieved instead as an observation, never a score. It needs only
the embedding model, not the answering model, so it is far cheaper than `make eval`.

**A symptom-only retrieval index** (`InMemoryRetriever(symptom_index=...)`,
`--symptom-index` on the benchmark): embeds each history source's evidence alone, with its
recorded cause and fix left out of the vector, while the text the model is shown is
unchanged. `Source.symptom_text` carries the split; sources with none (hand-built in tests)
fall back to the full text.

## Consequences

**Measured, `triage.retrieval_metrics` against the real data set** (`nomic-embed-text`
embeddings, 39 questions against 32 distinct history sources, top 5 retrieved):

- **All history, full-text index:** Recall@1 36/39 (0.92), Recall@3 and @5 both 39/39,
  MRR@5 0.96. The one fault with no hit at rank 1 is `reset_leaks_offset` (0/3 @1, 3/3 @3):
  its three history runs are near-duplicates of `fixture_skips_reset`'s single indexed run
  (ADR 0012 indexes identical runs once), so the closer-worded `fixture_skips_reset` source
  outranks it until rank 3.
- **All history, symptom-only index:** Recall@1 39/39 (1.00) — the `reset_leaks_offset` miss
  disappears when the cause and fix sentences are left out of the embedded text. Evidence
  that stripping the resolution from the index, not just the model's prompt, measurably
  helps the nearest-neighbour search on this data set.
- **Own fault held out** and **category held out:** both 0 of 39 by construction (the
  question's relevant sources are excluded from the index before the search runs); the
  report's "retrieved instead" section shows every held-out query still returns a
  plausible-looking, differently-faulted source in rank order — the same failure mode the
  disagreement gate (ADR 0013) exists to catch downstream.

**Measured, `triage.evaluate` against the real data set** (`qwen2.5:3b`, local, temperature
0, `triage/results/rag.md`):

- **Stripping the raw log lines changes no fault's score.** "Full evidence, no logs" solves
  the same 8 of 13 as full evidence, with an identical per-fault breakdown. The "No signal
  reaching the analyzer" note (ADR 0017) lives outside the stripped `Logs:` block, so the
  one signal the ablations had already shown to matter survives; the raw log lines
  themselves add nothing the rest of the evidence doesn't already state.
- **Stripping the sequence log also leaves the count at 8 of 13, but changes which ones.**
  `config_decimal_error` drops from 3/3 to 0/3 — it is one of the four faults whose cause
  appears only in the sequence log (`triage/data/README.md`) — while `config_parameter_out_
  of_range` rises from 0/3 to 3/3: reading the failed test directly, instead of the setup
  step blamed for it, removes a misleading attribution for that fault. The model also
  answers `UNKNOWN` on its own for 6 of 39 questions here, with no check or gate involved,
  where it never did on full evidence.
- **Category hold-out is much harder than fault hold-out, as designed.** "Full + retrieval
  + checks, category held out" solves 1 of 13 (3 of 39 right, 24 `UNKNOWN`, 12 wrong),
  against 4 of 13 for the existing own-fault-held-out row. The majority vote fares worse
  still: 0 of 13 with its whole category gone, against 5 of 13 with only its own fault
  gone.
- **One fault scores *better* with more held out: `link_down`.** Own-fault-held-out scores
  0 of 3: offered only a same-category look-alike (`analyzer_needs_alignment`), the model
  copies its "recalibrated" fix into a wrong `ALIGNMENT` answer — the failure mode ADR 0017
  already named. Category-held-out removes that look-alike along with everything else
  bench-related, and the model falls back to the evidence alone, which already solves
  `link_down` 3 of 3. Holding out more can score better when what it removes was actively
  misleading, not merely unhelpful.

## Follow-ups

- The symptom-only index is wired through `InMemoryRetriever` and the benchmark CLI but not
  yet through `triage.evaluate` or `triage.service`; switching the model pipeline's
  retriever to it is a follow-up once the retrieval-only result is corroborated by the
  classification score.
- `docs/EVALUATION.md`'s results chart and "What the numbers say" prose are not yet updated
  with these scenarios; that is a separate, more visual piece of follow-up work.
- `make retrieval-eval` is not yet in CI; `make eval` already runs on the `eval-ceiling`
  label (ADR 0024), and this benchmark could run alongside it once it has earned a place in
  the regular pull-request checks.
