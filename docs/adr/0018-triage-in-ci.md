# ADR 0018: Triage a failed bench run in CI, and put the verdict in the pull request comment

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

The project's question is "a test failed: was it the firmware, the bench or the test, and
what fixed it last time?". Until now the assistant answered it only when someone ran
`make triage` or called the API. The place the question comes up is a pull request whose
bench job just failed.

## Decisions

- **A `Triage` job runs only when the bench job failed**, and is allowed to fail
  (`continue-on-error`, 20-minute timeout). A passing pull request never pays for it, and a
  triage problem never turns CI red.
- **The bench job saves the failed run** as a run folder (`tools/save_failed_run.py`): the
  suite's results, the last 300 lines of `docker compose logs` (the tail `tools/fault_run.py`
  keeps) and the sequence log, without a label. It is uploaded as the `failed-run` artifact.
- **The same pipeline as the API** (`tools/ci_triage.py`): the three closest runs from the
  committed history, the disagreement gate, `qwen2.5:3b`, then the checks. CI and the API
  give the same answer to the same run, and that answer may be `UNKNOWN`.
- **Ollama runs on the runner, on localhost.** The release is pinned and its checksum
  verified, like Trivy and gitleaks; only its CPU libraries are kept, and they are cached
  with both models under one key, so the download happens once per version.
- **A section of the existing comment**, between the test results and the job table: the
  action, its cause (firmware, bench or test), the model's reason, the cited past runs and
  each one's recorded fix, quoted, with identical fixes on one line. When a gate or a check
  stood in for the model, its reason replaces the model's words, which are no longer the
  answer. A missing verdict file means the job didn't finish, and the section says so.
- **Logs are untrusted.** A pull request's code writes the logs, so it can steer what the
  model writes. Every piece of text that isn't fixed by `tools/ci_triage.py` is shown as
  one inline code span on one line, with backticks replaced and the length cut to 300
  characters: GitHub never reads a code span as a mention, link, image, heading or HTML.
  The action is a key from a fixed list; the model still has no tools.

## Consequences

- On the 8 GB M1, saving and triaging a recorded run took 61 to 73 s, most of it embedding
  the 27 history runs and one uncached prompt. The time on a GitHub runner is not yet
  measured.
- Someone with write access can change `tools/ci_triage.py` in their own pull request; the
  code-span rule guards against text in the logs, not against the person who wrote the code.
  The comment is posted for pull requests from this repository only, as before.
- A bench failure before the suite wrote results leaves no run; the section says the bench
  failed before a run was saved.
