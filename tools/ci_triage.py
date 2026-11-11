"""Triage CI's failed bench run and write the verdicts as a section of the pull request comment.

The run's failed tests are split into distinct failures (triage.failures), and each of the
first few is triaged on its own with the pipeline the API runs: the closest past runs from
the committed history, the disagreement gate, the model, then the checks. Each failure gets
its own block saying what to do, even when two get the same answer. The run comes from the
pull request's own code, so its logs are untrusted and can steer what the model writes;
every piece of text that isn't fixed by this module is shown as inline code on one line,
which GitHub never reads as a mention, link, image or HTML. The action itself is one key
from a fixed list.

    python -m tools.ci_triage reports/failed-run --out reports/triage.md
"""

import argparse
import sys
from collections.abc import Sequence
from functools import partial
from pathlib import Path

from triage.answer import Verdict, grouped_past_fixes, verdict_schema
from triage.evidence import SavedRun
from triage.failures import (
    DEFAULT_MAX_FAILURES,
    FailureAnswer,
    RunTriage,
    triage_failures,
    triage_like_the_api,
)
from triage.llm import DEFAULT_MODEL, LLMProvider, ModelUnavailableError, OllamaProvider
from triage.pack import DEFAULT_PACK_FILE, UNKNOWN, PackError, load_pack
from triage.retrieval import DEFAULT_HISTORY, Retriever, history_index
from triage.textfmt import clip

HEADING = "### Triage of the failed bench run"
"""The section's first line."""

SHOWN_TESTS = 3
"""How many of an answer's tests are named before the rest are counted."""

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


NO_SAFE_ANSWER = "no safe answer, so look at this failure by hand"
"""What to do when triage has no action for a failure; fixed text, never the model's."""


def verdict_lines(verdict: Verdict) -> list[str]:
    """Write the bullets for one verdict: the action, what to do, and why.

    Args:
        verdict: What triage decided.

    Returns:
        The bullets. Every verdict says what to do, UNKNOWN included. When a gate or a
        check stood in for the model, its reason replaces the model's words, which are no
        longer the answer; an overruled answer's past fixes go too, since they are fixes
        for the action the check refused.
    """
    to_do = NO_SAFE_ANSWER if verdict.fix is UNKNOWN else verdict.fix.says
    lines = [
        f"- **Answer:** **{code(verdict.fix.key)}** · cause: **{verdict.category}**",
        f"- **What to do:** {to_do}",
    ]
    if verdict.gate_reason:
        lines.append(f"- **Model not asked:** {code(verdict.gate_reason)}")
    elif verdict.overruled:
        lines.append(f"- **Overruled by a check:** {code(verdict.overruled)}")
    else:
        lines.append(f"- **Why:** {code(verdict.because)}")
        lines.append(f"- **Where:** {code(verdict.where)}")
        lines.append(f"- **Cited:** {codes(verdict.cited)}")
    if verdict.overruled:
        return lines
    lines += [
        f"- **Last time:** {code(fix_applied)} ({codes(source_ids)})"
        for fix_applied, source_ids in grouped_past_fixes(verdict)
    ]
    return lines


def _plural(count: int, noun: str) -> str:
    """Write a count with its noun, adding an s unless the count is one.

    Args:
        count: The count.
        noun: The noun, singular.

    Returns:
        For example '1 test' or '3 tests'.
    """
    return f"{count} {noun if count == 1 else noun + 's'}"


def tests_line(test_ids: Sequence[str]) -> str:
    """List the tests an answer covers: the first few by name, then how many more.

    Args:
        test_ids: The tests' names, which the pull request's code chose, so untrusted.

    Returns:
        One bullet line.
    """
    shown = codes(test_ids[:SHOWN_TESTS])
    more = len(test_ids) - SHOWN_TESTS
    return f"- **Tests ({len(test_ids)}):** {shown}" + (f" and {more} more" if more > 0 else "")


def answer_lines(answer: FailureAnswer, number: int, count: int) -> list[str]:
    """Write one failure's block: what failed, its tests, and what to do about it.

    Args:
        answer: The failure's answer.
        number: Its place among the failures, from 1.
        count: How many failures the run has.

    Returns:
        The lines, led by a heading naming the failure.
    """
    heading = f"#### Failure {number} of {count}: {code(answer.group.description)}"
    lines = [heading, "", tests_line(answer.group.test_ids)]
    if answer.verdict is not None:
        return lines + verdict_lines(answer.verdict)
    return [
        *lines,
        "- **Answer:** none",
        f"- **What to do:** {NO_SAFE_ANSWER}",
        f"- **Problem:** {code(answer.problem)}",
    ]


def summary_line(result: RunTriage) -> str:
    """Say how the failed tests split into distinct failures.

    Args:
        result: The run's triage.

    Returns:
        One line of fixed text and counts.
    """
    failures = _plural(result.distinct_failures, "distinct failure")
    line = f"{_plural(result.failed_tests, 'failed test')}: {failures}"
    if result.distinct_failures == 1:
        return line + "."
    return line + ", each triaged on its own below."


def render_triage(result: RunTriage) -> str:
    """Write the section: a summary, one block per failure triaged, and any left over.

    Args:
        result: The run's triage.

    Returns:
        The section's Markdown.
    """
    lines = [HEADING, "", summary_line(result)]
    for number, answer in enumerate(result.answers, start=1):
        lines += ["", *answer_lines(answer, number, result.distinct_failures)]
    if result.untriaged:
        skipped = ", ".join(
            f"{code(group.description)} ({_plural(len(group.tests), 'test')})"
            for group in result.untriaged
        )
        cap = _plural(result.limit, "failure")
        lines += ["", f"**Not triaged** (at most {cap} per run): {skipped}"]
    lines += ["", FOOTER]
    return "\n".join(lines) + "\n"


def render_problem(reason: str) -> str:
    """Write the section when there is no verdict.

    Args:
        reason: Why there is none, in one line.

    Returns:
        The section's Markdown.
    """
    return f"{HEADING}\n\nNo verdict: {code(reason)}\n"


def positive_count(text: str) -> int:
    """Read a command-line count that must be at least one.

    Args:
        text: The argument as typed.

    Returns:
        The count.

    Raises:
        argparse.ArgumentTypeError: If it isn't a whole number of at least one.
    """
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f"expected a whole number of at least 1, got {text!r}")
    return int(text)


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
        0 with an answer for each failure triaged, even one whose reply couldn't be used;
        1 when the pack can't be used, the run is missing or has no failed test, or the
        model or embedder can't be reached.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci_triage", description="Triage CI's failed bench run."
    )
    parser.add_argument("run", type=Path, help="the saved run folder")
    parser.add_argument("--out", type=Path, required=True, help="the Markdown file to write")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="history runs")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model to ask")
    parser.add_argument(
        "--pack", type=Path, default=DEFAULT_PACK_FILE, help="domain pack (default: the RF bench)"
    )
    parser.add_argument(
        "--max-failures",
        type=positive_count,
        default=DEFAULT_MAX_FAILURES,
        help=f"distinct failures to triage, each one prompt (default: {DEFAULT_MAX_FAILURES})",
    )
    parsed = parser.parse_args(arguments)
    try:
        pack = load_pack(parsed.pack)
        model = provider or OllamaProvider(
            model=parsed.model, response_format=verdict_schema(pack=pack)
        )
        run = SavedRun.load(parsed.run)
        index = retriever or history_index(parsed.history)
        answer = partial(triage_like_the_api, provider=model, retriever=index, pack=pack)
        result = triage_failures(run, answer, parsed.max_failures)
        section, exit_code = render_triage(result), 0
        if not result.distinct_failures:
            section, exit_code = render_problem("the saved run has no failed test to triage"), 1
    except FileNotFoundError:
        section, exit_code = render_problem("the bench failed before the suite saved a run"), 1
    except (ModelUnavailableError, PackError) as error:
        section, exit_code = render_problem(str(error)), 1
    parsed.out.parent.mkdir(parents=True, exist_ok=True)
    parsed.out.write_text(section, encoding="utf-8")
    sys.stdout.write(section)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
