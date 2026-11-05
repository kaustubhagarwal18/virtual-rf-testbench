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

from triage.answer import MalformedAnswerError, parse_verdict
from triage.evidence import ERROR_ONLY, FULL, LEVELS, SavedRun, build_evidence, first_failure
from triage.llm import DEFAULT_MODEL, LLMProvider, OllamaProvider
from triage.prompt import Fix, build_prompt

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
        error: The failure the model was shown, as one line.
        answer: What the model said: its chosen fix, where and why, or the unusable reply.
    """

    fault: str
    expected: Fix
    chosen: str
    right: bool
    error: str = ""
    answer: str = ""


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
        The outcome, which keeps the failure the model was shown and what it answered, so a
        report can show both; a reply that couldn't be parsed counts as MALFORMED, not right.
    """
    expected = expected_fix(run)
    failure = first_failure(run)
    error = (failure.detail or failure.where) if failure is not None else "every test passed"
    evidence = build_evidence(run, level)
    system, user = build_prompt(evidence)
    reply = provider.complete(system, user)
    try:
        verdict = parse_verdict(reply)
    except MalformedAnswerError as unusable:
        return Outcome(run.folder.name, expected, MALFORMED, False, error, f"unusable: {unusable}")
    answer = f"{verdict.fix.name} ({verdict.where}) because {verdict.because}"
    return Outcome(
        run.folder.name, expected, verdict.fix.name, verdict.fix is expected, error, answer
    )


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


PIPELINE_DIAGRAM = """```mermaid
flowchart LR
    run["Saved run<br/>results, logs, sequence"] --> ev["evidence.py<br/>first failure"]
    ev --> pr["prompt.py<br/>instructions + fix list"]
    pr --> llm["qwen2.5:3b<br/>(Ollama, local)"]
    llm --> an["answer.py<br/>parse + refuse unusable"]
    an --> score["evaluate.py<br/>right fix?"]
    label["label.json"] -. answer key, never shown .-> score
```"""
"""The pipeline, drawn once for the report. The label reaches only the scoring, never qwen."""


def _clip(text: str, limit: int) -> str:
    """Shorten text to a limit at a word boundary, so no word is cut mid-way.

    Args:
        text: The text to shorten.
        limit: The longest result, before the ellipsis.

    Returns:
        The text, or its first whole words within the limit followed by an ellipsis.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    clipped = collapsed[:limit].rsplit(" ", 1)[0]
    return f"{clipped}..."


def render_report(results: Sequence[ScenarioResult], model: str) -> str:
    """Write a detailed Markdown report: the pipeline, the scores, and every question's answer.

    Args:
        results: The scenario results.
        model: The model that answered.

    Returns:
        The report, showing for each question the failure the model saw and what it answered.
    """
    total = len(results[0].outcomes) if results else 0
    lines = [
        "# Triage baseline (no retrieval)",
        "",
        f"Model: `{model}`, temperature 0, fixed seed. Scored on whether the model named the "
        "fix that resolves each failure. Counts, not percentages: nine questions.",
        "",
        PIPELINE_DIAGRAM,
        "",
    ]
    for result in results:
        lines += [f"## {SCENARIOS.get(result.level, result.level)}: {result.right} of {total}", ""]
        lines += [
            "| Fault | Expected | Chosen | | Error shown → the model's answer |",
            "|---|---|---|---|---|",
        ]
        for outcome in result.outcomes:
            mark = "✓" if outcome.right else "✗"
            detail = f"`{_clip(outcome.error, 90)}` -> {_clip(outcome.answer, 130)}"
            lines.append(
                f"| {outcome.fault} | {outcome.expected.name} | {outcome.chosen} | {mark} | "
                f"{detail} |"
            )
        if result.wrong_choices:
            tally = ", ".join(
                f"{choice} x{count}" for choice, count in result.wrong_choices.items()
            )
            lines += ["", f"Wrong choices: {tally}.", ""]
    lines += ["## Findings", ""]
    lines += [f"- {finding}" for finding in findings(results)]
    lines += [
        "",
        "These are one machine's deterministic numbers (temperature 0, fixed seed); another "
        "machine's model may differ in wording and the odd verdict. A verdict whose reasoning "
        "is sound but whose category is wrong still counts as a miss, so the counts, if "
        "anything, understate the model.",
    ]
    return "\n".join(lines) + "\n"


def findings(results: Sequence[ScenarioResult]) -> list[str]:
    """Draw conclusions from the scores, computed so they can't drift from the data.

    Args:
        results: The scenario results.

    Returns:
        One sentence per finding: the best scenario, whether more evidence helped, the most
        common wrong choice, and which faults were solved in every scenario.
    """
    total = len(results[0].outcomes) if results else 0
    conclusions: list[str] = []
    best = max(results, key=lambda result: result.right)
    conclusions.append(
        f"Best scenario: {SCENARIOS.get(best.level, best.level)}, {best.right} of {total}."
    )
    if len(results) > 1:
        first, last = results[0], results[-1]
        verb = (
            "raised"
            if last.right > first.right
            else "lowered"
            if last.right < first.right
            else "left"
        )
        conclusions.append(
            f"More evidence {verb} the score ({first.right} to {last.right} of {total}): on "
            "this model, fuller context did not help."
            if verb != "raised"
            else f"More evidence {verb} the score ({first.right} to {last.right} of {total})."
        )
    wrong: dict[str, int] = {}
    for result in results:
        for choice, count in result.wrong_choices.items():
            wrong[choice] = wrong.get(choice, 0) + count
    if wrong:
        choice, count = max(wrong.items(), key=lambda item: item[1])
        conclusions.append(
            f"Most common wrong choice: {choice} ({count} times across the scenarios); the "
            "model falls back to one answer when unsure."
        )
    solved = sorted(
        {outcome.fault for outcome in results[0].outcomes}.intersection(
            *({outcome.fault for outcome in result.outcomes if outcome.right} for result in results)
        )
    )
    conclusions.append(
        f"Solved in every scenario: {', '.join(solved) or 'none'} — the failures whose error "
        "text names the fix almost literally."
    )
    return conclusions


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
    parser.add_argument(
        "--report", type=Path, help="also write a detailed Markdown report to this file"
    )
    parsed = parser.parse_args(arguments)
    provider = provider or OllamaProvider()
    levels = parsed.level or list(SCENARIOS)
    results = evaluate(parsed.questions, provider, levels)
    model = getattr(provider, "model", DEFAULT_MODEL)
    sys.stdout.write(render(results, model))
    if parsed.report is not None:
        parsed.report.parent.mkdir(parents=True, exist_ok=True)
        parsed.report.write_text(render_report(results, model), encoding="utf-8")
        sys.stdout.write(f"\nwrote {parsed.report}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
