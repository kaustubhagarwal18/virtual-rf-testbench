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

from triage.answer import VERDICT_SCHEMA, MalformedAnswerError
from triage.embed import OllamaEmbedder
from triage.evaluate import expected_fix
from triage.evidence import FULL, SavedRun, error_only_evidence
from triage.label import load_label
from triage.llm import DEFAULT_MODEL, LLMProvider, ModelUnavailableError, OllamaProvider
from triage.prompt import Fix, Source
from triage.retrieval import DEFAULT_HISTORY, DEFAULT_TOP_K, InMemoryRetriever, Retriever
from triage.service import triage_run
from triage.textfmt import clip

DEFAULT_RUN = Path("triage/data/runs/questions/cable_loss-1")
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
    """

    chosen: str
    where: str
    because: str
    cited: tuple[str, ...]
    right: bool


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
    expected: Fix
    error: str
    baseline: Answer
    retrieval: Answer


def _answer(
    run: SavedRun, provider: LLMProvider, expected: Fix, sources: Sequence[Source]
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
        chosen=verdict.fix.name,
        where=verdict.where,
        because=verdict.because,
        cited=tuple(verdict.cited),
        right=verdict.fix is expected,
    )


def run_demo(
    run: SavedRun, provider: LLMProvider, retriever: Retriever, top_k: int = DEFAULT_TOP_K
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


def _answer_lines(title: str, answer: Answer, expected: Fix) -> list[str]:
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
    expected_note = "" if answer.right else f" (resolving fix: {expected.name})"
    return [
        f"{title}:",
        f"  fix:   {answer.chosen}  [{mark}]{expected_note}",
        f"  where: {answer.where or '-'}",
        f"  why:   {clip(answer.because, 160)}",
        f"  cited: {cited}",
    ]


def render(result: DemoResult) -> str:
    """Write the two answers as a readable side-by-side, with a closing line on the change.

    Args:
        result: The demo result.

    Returns:
        The report.
    """
    lines = [
        f"Run: {result.fault}  (resolving fix: {result.expected.name} — {result.expected.value})",
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
        verb = "to the resolving fix" if result.retrieval.right else "but not to the resolving fix"
        lines.append(
            f"Retrieval changed the verdict from {result.baseline.chosen} "
            f"to {result.retrieval.chosen} {verb}."
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
        its label, or the history folder is missing or names a fault with no known fix.
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
    provider = provider or OllamaProvider(model=parsed.model, response_format=VERDICT_SCHEMA)
    try:
        run = SavedRun.load(parsed.run)
        if retriever is None:
            retriever = InMemoryRetriever.from_history(  # pragma: no cover - needs Ollama
                parsed.history, OllamaEmbedder()
            )
        result = run_demo(run, provider, retriever)
    except (ModelUnavailableError, FileNotFoundError, KeyError) as error:
        sys.stdout.write(f"{error}\n")
        return 1
    sys.stdout.write(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
