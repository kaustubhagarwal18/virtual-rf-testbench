"""Show, side by side, what retrieval adds to the triage assistant on one failed run.

The same run is triaged twice with the same evidence: once with no past runs, and once with
the most similar past runs offered as sources. Only retrieval changes between the two, so the
difference in the verdict is what retrieval bought. The run's label is read only to print the
resolving fix and mark each verdict right or wrong; it is never put in a prompt.

`make triage-demo RUN=<folder>` runs it against a local Ollama model.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from triage.answer import VERDICT_SCHEMA, MalformedAnswerError, past_fix_lines
from triage.bench_run import load_case
from triage.case import Case, CaseError
from triage.evaluate import expected_fix
from triage.evidence import FULL, error_only_evidence
from triage.label import load_label
from triage.llm import DEFAULT_MODEL, LLMProvider, ModelUnavailableError, OllamaProvider
from triage.pack import Action
from triage.prompt import Source
from triage.retrieval import DEFAULT_HISTORY, DEFAULT_TOP_K, Retriever, history_index
from triage.service import triage_run
from triage.textfmt import clip

DEFAULT_RUN = Path("triage/data/runs/questions/config_parameter_out_of_range-1")
"""The run the demo shows when none is named: retrieval flips its verdict to the right fix."""

UNUSABLE = "unusable"
"""The chosen-action label for a reply that could not be parsed, so the demo shows it as a
result rather than crashing: an unusable answer is a real triage outcome."""


@dataclass(frozen=True)
class Answer:
    """One triage answer in the demo.

    Attributes:
        chosen: The action the model chose, or UNUSABLE when the reply could not be parsed.
        where: What to act on, in the model's words; empty for an unusable reply.
        because: The model's one-sentence reason, or the parser's complaint when unusable.
        cited: The source ids the model leaned on; empty when it cited none or was unusable.
        right: Whether the chosen action is the one that resolves the fault.
        category: Firmware, bench, test or unknown, from the chosen action; empty when
            unusable.
        last_time: What fixed the cited past runs, quoted from their records: each distinct
            fix once, beside the ids of the runs that recorded it.
    """

    chosen: str
    where: str
    because: str
    cited: tuple[str, ...]
    right: bool
    category: str = ""
    last_time: tuple[str, ...] = ()


@dataclass(frozen=True)
class DemoResult:
    """Both answers for one run, with the key the demo marks them against.

    Attributes:
        fault: The fault the run holds, named in the report.
        expected: The action that resolves it, read from the label.
        error: The first failure's message, the one line both answers started from.
        baseline: The verdict with no past runs offered.
        retrieval: The verdict with the most similar past runs offered.
    """

    fault: str
    expected: Action
    error: str
    baseline: Answer
    retrieval: Answer


def _answer(
    run: Case, provider: LLMProvider, expected: Action, sources: Sequence[Source]
) -> Answer:
    """Triage one run once, turning an unusable reply into a result instead of an error.

    Args:
        run: The run being triaged.
        provider: The answering model.
        expected: The action that resolves the fault, for marking the answer right or wrong.
        sources: The past runs offered to the model; empty for the baseline answer.

    Returns:
        The answer, with chosen set to UNUSABLE when the reply could not be parsed.
    """
    try:
        verdict = triage_run(run, provider, FULL, sources)
    except MalformedAnswerError as unusable:
        return Answer(UNUSABLE, "", str(unusable), (), right=False)
    return Answer(
        chosen=verdict.fix.key,
        where=verdict.where,
        because=verdict.because,
        cited=tuple(verdict.cited),
        right=verdict.fix is expected,
        category=verdict.category,
        last_time=tuple(past_fix_lines(verdict)),
    )


def run_demo(
    run: Case, provider: LLMProvider, retriever: Retriever, top_k: int = DEFAULT_TOP_K
) -> DemoResult:
    """Triage one run with and without retrieval, so the difference is attributable to it.

    Both answers see the same full evidence; only the offered past runs differ.

    Args:
        run: The run being triaged.
        provider: The answering model.
        retriever: Where the most similar past runs come from.
        top_k: How many past runs to offer the retrieval answer.

    Returns:
        The two answers and the key they are marked against.
    """
    expected = expected_fix(run)
    error = error_only_evidence(run)
    baseline = _answer(run, provider, expected, ())
    retrieval = _answer(run, provider, expected, retriever.similar_to(run, top_k))
    return DemoResult(str(load_label(run)["fault"]), expected, error, baseline, retrieval)


def _answer_lines(title: str, answer: Answer, expected: Action) -> list[str]:
    """Format one answer as a titled block.

    Args:
        title: The block's heading, such as "Without retrieval".
        answer: The answer to show.
        expected: The resolving action, for the right-or-wrong mark.

    Returns:
        The lines of the block.
    """
    mark = "RIGHT" if answer.right else "wrong"
    cited = ", ".join(answer.cited) if answer.cited else "nothing"
    expected_note = "" if answer.right else f" (resolving fix: {expected.key})"
    return [
        f"{title}:",
        f"  fix:   {answer.chosen}  [{mark}]{expected_note}",
        f"  where: {answer.where or '-'}",
        f"  why:   {clip(answer.because, 160)}",
        f"  cause: {answer.category or '-'}",
        f"  cited: {cited}",
        *(f"  last time: {fix}" for fix in answer.last_time),
    ]


def render(result: DemoResult) -> str:
    """Write the two answers as a readable side-by-side, with a closing line on the change.

    Args:
        result: The demo result.

    Returns:
        The report.
    """
    lines = [
        f"Run: {result.fault}  (resolving fix: {result.expected.key} — {result.expected.says})",
        f"Error shown to the model: {clip(result.error, 120)}",
        "",
        *_answer_lines("Without retrieval (evidence alone)", result.baseline, result.expected),
        "",
        *_answer_lines(
            "With retrieval (most similar past runs offered)", result.retrieval, result.expected
        ),
        "",
    ]
    if result.baseline.chosen != result.retrieval.chosen:
        verdict = "the resolving fix" if result.retrieval.right else "not the resolving fix"
        lines.append(
            f"Retrieval changed the verdict from {result.baseline.chosen} "
            f"to {result.retrieval.chosen}, {verdict}."
        )
    else:
        lines.append(f"Retrieval did not change the verdict ({result.baseline.chosen}).")
    return "\n".join(lines) + "\n"


def main(
    arguments: Sequence[str] | None = None,
    provider: LLMProvider | None = None,
    retriever: Retriever | None = None,
) -> int:
    """Run the demo from the command line.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        provider: The answering model; None uses Ollama with the model named by --model.
        retriever: The source of past runs; None builds an in-memory index from the history
            runs with the Ollama embedder.

    Returns:
        0 when the demo ran; 1 when the model or embedder could not be reached, or the run,
        its label, or the history folder is missing, malformed or names a fault with no
        known fix.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tools.triage_demo",
        description="Show what retrieval adds to triage on one run.",
    )
    parser.add_argument("run", type=Path, nargs="?", default=DEFAULT_RUN, help="a saved run folder")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="history runs")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Ollama model to ask (default: {DEFAULT_MODEL})"
    )
    parsed = parser.parse_args(arguments)
    provider = (
        provider
        if provider is not None
        else OllamaProvider(model=parsed.model, response_format=VERDICT_SCHEMA)
    )
    try:
        run = load_case(parsed.run)
        result = run_demo(
            run, provider, retriever if retriever is not None else history_index(parsed.history)
        )
    except (ModelUnavailableError, FileNotFoundError, KeyError, CaseError) as error:
        sys.stdout.write(f"{error}\n")
        return 1
    sys.stdout.write(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
