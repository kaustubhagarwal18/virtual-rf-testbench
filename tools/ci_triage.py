"""Triage CI's failed bench run and write the verdict as a section of the pull request comment.

It runs the pipeline the API runs: the closest past runs from the committed history, the
disagreement gate, the model, then the checks. The run comes from the pull request's own
code, so its logs are untrusted and can steer what the model writes; every piece of text
that isn't fixed by this module is shown as inline code on one line, which GitHub never
reads as a mention, link, image or HTML. The action itself is one key from a fixed list.

    python -m tools.ci_triage reports/failed-run --out reports/triage.md
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from triage.abstain import SERVICE_GATES
from triage.answer import VERDICT_SCHEMA, MalformedAnswerError, PastFix, Verdict
from triage.checks import DEFAULT_CHECKS
from triage.evidence import FULL, SavedRun
from triage.llm import DEFAULT_MODEL, LLMProvider, ModelUnavailableError, OllamaProvider
from triage.retrieval import DEFAULT_HISTORY, DEFAULT_TOP_K, Retriever, history_index
from triage.service import triage_run
from triage.textfmt import clip

HEADING = "### Triage of the failed bench run"
"""The section's first line."""

TEXT_LIMIT = 300
"""The longest piece of untrusted text shown, in characters, before it is cut."""

FOOTER = (
    "<sub>Answered by a local model on the CI runner, from the committed history of resolved"
    " runs, with the checks and the disagreement gate the API applies. UNKNOWN means it"
    " found no safe answer. The model's words are shown as code.</sub>"
)
"""What produced the verdict, so a reviewer knows how far to trust it."""


def code(text: str) -> str:
    """Show untrusted text as one inline code span on one line.

    Args:
        text: Text the model or a record wrote.

    Returns:
        The text, collapsed to one line, cut to TEXT_LIMIT, with backticks replaced so the
        span can't be closed early; '(none)' when there is no text.
    """
    collapsed = clip(text.replace("`", "'"), TEXT_LIMIT)
    return f"`{collapsed}`" if collapsed else "(none)"


def codes(texts: Sequence[str]) -> str:
    """Show several untrusted texts, such as source ids, as code spans separated by commas.

    Args:
        texts: The texts.

    Returns:
        The code spans, or '(none)' when there are no texts.
    """
    return ", ".join(code(text) for text in texts) or "(none)"


def render_verdict(verdict: Verdict) -> str:
    """Write the section for a verdict.

    Args:
        verdict: What triage decided.

    Returns:
        The section's Markdown. When a gate or a check stood in for the model, its reason
        replaces the model's words, which are no longer the answer.
    """
    lines = [HEADING, "", f"**{code(verdict.fix.name)}** · cause: **{verdict.category}**", ""]
    if verdict.gate_reason:
        lines.append(f"- **Model not asked:** {code(verdict.gate_reason)}")
    elif verdict.overruled:
        lines.append(f"- **Overruled by a check:** {code(verdict.overruled)}")
    else:
        lines.append(f"- **Why:** {code(verdict.because)}")
        lines.append(f"- **Where:** {code(verdict.where)}")
        lines.append(f"- **Cited:** {codes(verdict.cited)}")
    lines += [
        f"- **Last time:** {code(fix_applied)} ({codes(source_ids)})"
        for fix_applied, source_ids in _group_past_fixes(verdict.past_fixes).items()
    ]
    lines += ["", FOOTER]
    return "\n".join(lines) + "\n"


def _group_past_fixes(past_fixes: Sequence[PastFix]) -> dict[str, list[str]]:
    """Group the cited past runs by their recorded fix, so one fix is listed once.

    Args:
        past_fixes: Each cited run's recorded fix, in citation order.

    Returns:
        Each distinct fix, in first-cited order, with the ids of the runs it fixed.
    """
    grouped: dict[str, list[str]] = {}
    for past in past_fixes:
        grouped.setdefault(past.fix_applied, []).append(past.source_id)
    return grouped


def render_problem(reason: str) -> str:
    """Write the section when there is no verdict.

    Args:
        reason: Why there is none, in one line.

    Returns:
        The section's Markdown.
    """
    return f"{HEADING}\n\nNo verdict: {code(reason)}\n"


def triage_failed_run(run: SavedRun, provider: LLMProvider, retriever: Retriever) -> Verdict:
    """Triage the run as the API does: past runs, the disagreement gate, the model, the checks.

    Args:
        run: The failed run.
        provider: The answering model.
        retriever: The source of past runs.

    Returns:
        The verdict, UNKNOWN when the gate or a check found no safe answer.
    """
    sources = retriever.similar_to(run, DEFAULT_TOP_K)
    return triage_run(run, provider, FULL, sources, checks=DEFAULT_CHECKS, gates=SERVICE_GATES)


def main(
    arguments: Sequence[str] | None = None,
    provider: LLMProvider | None = None,
    retriever: Retriever | None = None,
) -> int:
    """Triage the run named on the command line and write its section.

    A section is always written, so the comment can explain a missing verdict.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        provider: The answering model; None uses Ollama with the model named by --model.
        retriever: The source of past runs; None indexes the history with the Ollama embedder.

    Returns:
        0 with a verdict; 1 when the run is missing, the model or embedder can't be reached,
        or its reply can't be used.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci_triage", description="Triage CI's failed bench run."
    )
    parser.add_argument("run", type=Path, help="the saved run folder")
    parser.add_argument("--out", type=Path, required=True, help="the Markdown file to write")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="history runs")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model to ask")
    parsed = parser.parse_args(arguments)
    provider = provider or OllamaProvider(model=parsed.model, response_format=VERDICT_SCHEMA)
    try:
        run = SavedRun.load(parsed.run)
        verdict = triage_failed_run(run, provider, retriever or history_index(parsed.history))
        section, exit_code = render_verdict(verdict), 0
    except FileNotFoundError:
        section, exit_code = render_problem("the bench failed before the suite saved a run"), 1
    except (ModelUnavailableError, MalformedAnswerError) as error:
        section, exit_code = render_problem(str(error)), 1
    parsed.out.parent.mkdir(parents=True, exist_ok=True)
    parsed.out.write_text(section, encoding="utf-8")
    sys.stdout.write(section)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
