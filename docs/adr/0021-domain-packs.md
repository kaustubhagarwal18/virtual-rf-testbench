# ADR 0021: Domain knowledge lives in a pack file, starting with the actions

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

The triage pipeline (evidence, retrieval, the gate, the model, the checks) is meant to serve
any domain where failures recur and someone records how each was fixed: wafer test, payment
batches, a device fleet, CI. But the RF bench was written into the code. The actions were a
Python enum in `triage/prompt.py`, their causes a table beside it, and the system prompt
began "You triage failed runs of an automated RF test bench". Seven modules used the enum as
a type. A second domain would have meant editing the core.

## Decisions

- **A domain pack is a TOML file** (`triage/packs/rf-bench.toml`), read with the standard
  library's `tomllib`, so no new dependency. It holds the domain's description for the
  model, its categories of cause, and its actions, each with a key, the cause it answers and
  what it says to do.
- **The core owns what every domain shares:** `UNKNOWN`, added to every pack and reserved;
  the rules paragraph of the system prompt (when to abstain, untrusted data, the answer's
  JSON shape); and the pack's checks on load. A broken pack is refused with one line naming
  the file: a repeated or reserved key, a lower-case key, a category the pack doesn't list,
  a missing field, no actions, or not TOML.
- **`Action` is a plain value** (`key`, `says`, `category`) replacing the enum. The pipeline
  still reads the default pack, `DEFAULT_PACK`; passing a pack through it comes with the
  checks and the case schema, which need the same plumbing.
- **A behaviour lock first** (`tests/unit/triage/test_behaviour_lock.py`). A full `make eval`
  needs a model and an embedder, so the refactor was checked without them: the test hashes
  231 parts of triage's behaviour (the system prompt, the answer schema, the evidence of all
  78 recorded runs at every level, the 32 past runs' texts, a prompt with past runs for each
  of the 39 questions, every check's verdict on every action, the majority vote, and
  parsing). It was recorded before the change and passes unchanged after it, so every
  prompt the model sees is byte for byte the same and the cached answers still apply. It
  fails on a one-word prompt edit or a one-comma change to a check's message.

## Consequences

- Adding an action for the RF bench is a pack edit, not a code change. Changing what an
  action says changes the prompt, so the lock fails until it is recorded again, on purpose.
- The checks still name RF actions and facts in code (`triage/checks.py`), the evidence
  builder still reads this repository's results format, and the API's label type still
  lists the RF keys (a test keeps it equal to the pack's). Those move next: the checks and
  facts into the pack, then a generic case schema with this format as one adapter.
