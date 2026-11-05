"""Score triage against the question runs: did it name the fix that resolves each failure?

One number per scenario — right fixes out of the questions asked — and a tally of what it
chose when it was wrong, because a confident wrong action is the failure mode that matters.
The scenarios differ only in what the model is told: the error message alone, or the whole
run. Retrieval is a later scenario and a later PR.

Nine questions give counts, not percentages: '6 of 9' is honest where '67%' is not.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from triage.answer import MalformedAnswerError
from triage.evidence import ERROR_ONLY, FULL, LEVELS, SavedRun
from triage.llm import DEFAULT_MODEL, LLMProvider, OllamaProvider
from triage.prompt import Fix
from triage.service import triage_run

DEFAULT_QUESTIONS = Path("triage/data/runs/questions")
MALFORMED = "malformed"
"""What a reply that couldn't be parsed counts as: neither right nor a real wrong answer."""

EXPECTED_FIX: dict[str, Fix] = {
    "cable_loss": Fix.CABLE,
    "link_down": Fix.LINK,
    "link_flaky": Fix.LINK,
    "slow_responses": Fix.TIMEOUT,
    "config_parameter_missing": Fix.CONFIGURATION,
    "config_parameter_out_of_range": Fix.CONFIGURATION,
    "config_decimal_error": Fix.CONFIGURATION,
    "reset_leaks_offset": Fix.FIRMWARE,
    "output_command_change": Fix.FIRMWARE,
}
"""The action that resolves each fault: the scoring key. ALIGNMENT and TEST_CLEANUP have no
fault of their own; they are there so a right answer can't come from a short list of options."""

SCENARIOS = {ERROR_ONLY: "error only", FULL: "full evidence"}
"""The evidence levels scored here. Retrieval is added as a third scenario in a later PR."""


@dataclass(frozen=True)
class Outcome:
    """One question asked once, in one scenario.

    Attributes:
        fault: The fault the run holds, which names the expected fix.
        expected: The fix that resolves it.
        chosen: The fix the model chose, or MALFORMED when the reply couldn't be used.
        right: Whether the chosen fix is the one that resolves the fault.
    """

    fault: str
    expected: Fix
    chosen: str
    right: bool


@dataclass
class ScenarioResult:
    """Every outcome for one scenario.

    Attributes:
        level: The evidence level.
        outcomes: One outcome per question asked.
    """

    level: str
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def right(self) -> int:
        """How many questions got the resolving fix.

        Returns:
            The count.
        """
        return sum(1 for outcome in self.outcomes if outcome.right)

    @property
    def wrong_choices(self) -> dict[str, int]:
        """What the model chose when it was wrong, counted.

        Returns:
            Each wrong or malformed choice and how often it was made.
        """
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            if not outcome.right:
                counts[outcome.chosen] = counts.get(outcome.chosen, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: -item[1]))


def expected_fix(run: SavedRun) -> Fix:
    """Return the fix that resolves a question run, from its label.

    The label is read here, and only here: it is the answer key, used to score the model's
    verdict, never shown to the model.

    Args:
        run: The question run.

    Returns:
        The resolving fix.

    Raises:
        KeyError: If the run's fault has no expected fix, which means a fault was added
            without saying how it is fixed.
    """
    label = json.loads((run.folder / "label.json").read_text(encoding="utf-8"))
    fault = label["fault"]
    if fault not in EXPECTED_FIX:
        raise KeyError(f"{fault} has no expected fix in EXPECTED_FIX")
    return EXPECTED_FIX[fault]


def evaluate_question(run: SavedRun, provider: LLMProvider, level: str) -> Outcome:
    """Ask about one run at one evidence level, and score the answer.

    Args:
        run: The question run.
        provider: The answering model.
        level: The evidence level.

    Returns:
        The outcome; a reply that couldn't be parsed counts as MALFORMED, not right.
    """
    expected = expected_fix(run)
    try:
        verdict = triage_run(run, provider, level)
    except MalformedAnswerError:
        return Outcome(run.folder.name, expected, MALFORMED, right=False)
    return Outcome(run.folder.name, expected, verdict.fix.name, verdict.fix is expected)


def evaluate(
    questions: Path, provider: LLMProvider, levels: Sequence[str] = tuple(SCENARIOS)
) -> list[ScenarioResult]:
    """Score every question run, in each scenario.

    Args:
        questions: The folder of question runs.
        provider: The answering model.
        levels: The evidence levels to score.

    Returns:
        One result per scenario, in the order given.
    """
    runs = [SavedRun.load(folder) for folder in sorted(questions.iterdir()) if folder.is_dir()]
    results = []
    for level in levels:
        result = ScenarioResult(level)
        for run in runs:
            result.outcomes.append(evaluate_question(run, provider, level))
        results.append(result)
    return results


def render(results: Sequence[ScenarioResult], model: str) -> str:
    """Write the scores as text, a scenario at a time.

    Args:
        results: The scenario results.
        model: The model that answered, named in the heading.

    Returns:
        The report.
    """
    total = len(results[0].outcomes) if results else 0
    lines = [f"Triage evaluation ({model}): did it name the fix that resolves the failure?", ""]
    for result in results:
        lines.append(f"## {SCENARIOS.get(result.level, result.level)}: {result.right} of {total}")
        for outcome in result.outcomes:
            mark = "ok  " if outcome.right else "MISS"
            lines.append(
                f"  {mark} {outcome.fault:32} expected {outcome.expected.name:13} "
                f"chose {outcome.chosen}"
            )
        if result.wrong_choices:
            tally = ", ".join(
                f"{choice} x{count}" for choice, count in result.wrong_choices.items()
            )
            lines.append(f"  wrong choices: {tally}")
        lines.append("")
    return "\n".join(lines)


def main(arguments: Sequence[str] | None = None, provider: LLMProvider | None = None) -> int:
    """Run the evaluation from the command line.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        provider: The answering model; None uses Ollama.

    Returns:
        0 when it ran, 1 if the model wasn't reachable.
    """
    parser = argparse.ArgumentParser(
        prog="python -m triage.evaluate", description="Score triage against the question runs."
    )
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS, help="question runs")
    parser.add_argument(
        "--level", choices=LEVELS, action="append", help="evidence level(s); default: both"
    )
    parsed = parser.parse_args(arguments)
    provider = provider or OllamaProvider()
    levels = parsed.level or list(SCENARIOS)
    results = evaluate(parsed.questions, provider, levels)
    model = getattr(provider, "model", DEFAULT_MODEL)
    sys.stdout.write(render(results, model))
    return 0


if __name__ == "__main__":
    sys.exit(main())
