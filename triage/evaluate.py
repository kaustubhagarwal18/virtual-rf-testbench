"""Score triage against the question runs: did it name the fix that resolves each failure?

One number per scenario — right fixes out of the questions asked — and a tally of what it
chose when it was wrong, because a confident wrong action is the failure mode that matters.
Three scenarios, each adding one thing: the error message alone, the whole run's evidence,
and that evidence plus the past runs most like it (retrieval). Any gain is attributable,
because only one thing changes between neighbours.

Nine questions give counts, not percentages: '6 of 9' is honest where '67%' is not.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from triage.answer import MalformedAnswerError, parse_verdict
from triage.embed import OllamaEmbedder
from triage.evidence import ERROR_ONLY, FULL, SavedRun, build_evidence, error_only_evidence
from triage.label import load_label
from triage.llm import DEFAULT_MODEL, LLMProvider, OllamaProvider
from triage.prompt import Fix, Source, build_prompt
from triage.retrieval import (
    DEFAULT_HISTORY,
    DEFAULT_TOP_K,
    InMemoryRetriever,
    Retriever,
)
from triage.textfmt import clip

DEFAULT_QUESTIONS = Path("triage/data/runs/questions")
MALFORMED = "malformed"
"""What a reply that couldn't be parsed counts as: neither right nor a real wrong answer."""

BASELINE_TITLE = "Triage baseline (no retrieval)"
"""The report heading when no scenario retrieves: the recorded floor in baseline.md."""

RETRIEVAL_TITLE = "Triage: retrieval against the no-retrieval baseline"
"""The report heading when a retrieval scenario is scored alongside the baseline ones."""

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


@dataclass(frozen=True)
class Scenario:
    """One way of asking the model, so scenarios can be compared.

    Attributes:
        name: What the scenario is called in the report.
        level: The evidence level the model is given.
        retrieve: Whether the past runs most like this one are offered as sources.
    """

    name: str
    level: str
    retrieve: bool = False


SCENARIOS = (
    Scenario("error only", ERROR_ONLY),
    Scenario("full evidence", FULL),
    Scenario("full + retrieval", FULL, retrieve=True),
)
"""The three scenarios, each adding exactly one thing to the one before."""


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
        cited: The history runs the model cited, if any; empty when it cited none or the
            reply was unusable. Kept so a finding can tell whether retrieval surfaced the
            matching past failure when the model still chose wrong.
    """

    fault: str
    expected: Fix
    chosen: str
    right: bool
    error: str = ""
    answer: str = ""
    cited: tuple[str, ...] = ()


@dataclass
class ScenarioResult:
    """Every outcome for one scenario.

    Attributes:
        name: The scenario's name, for the report.
        outcomes: One outcome per question asked.
    """

    name: str
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
    label = load_label(run)
    fault = label["fault"]
    if fault not in EXPECTED_FIX:
        raise KeyError(f"{fault} has no expected fix in EXPECTED_FIX")
    return EXPECTED_FIX[fault]


def evaluate_question(
    run: SavedRun, provider: LLMProvider, scenario: Scenario, retriever: Retriever | None
) -> Outcome:
    """Ask about one run in one scenario, and score the answer.

    Args:
        run: The question run.
        provider: The answering model.
        scenario: How to ask: the evidence level, and whether to retrieve past runs.
        retriever: Where retrieved sources come from; required when the scenario retrieves.

    Returns:
        The outcome, which keeps the failure the model was shown and what it answered, so a
        report can show both; a reply that couldn't be parsed counts as MALFORMED, not right.

    Raises:
        ValueError: If the scenario retrieves but no retriever was given.
    """
    expected = expected_fix(run)
    error = error_only_evidence(run)
    sources: tuple[Source, ...] = ()
    if scenario.retrieve:
        if retriever is None:
            raise ValueError(f"scenario {scenario.name!r} retrieves but no retriever was given")
        sources = tuple(retriever.similar_to(run, DEFAULT_TOP_K))
    system, user = build_prompt(build_evidence(run, scenario.level), sources)
    reply = provider.complete(system, user)
    try:
        verdict = parse_verdict(reply, [source.source_id for source in sources])
    except MalformedAnswerError as unusable:
        return Outcome(run.folder.name, expected, MALFORMED, False, error, f"unusable: {unusable}")
    cited = f" citing {', '.join(verdict.cited)}" if verdict.cited else ""
    answer = f"{verdict.fix.name} ({verdict.where}){cited} because {verdict.because}"
    return Outcome(
        run.folder.name,
        expected,
        verdict.fix.name,
        verdict.fix is expected,
        error,
        answer,
        cited=tuple(verdict.cited),
    )


def evaluate(
    questions: Path,
    provider: LLMProvider,
    scenarios: Sequence[Scenario] = SCENARIOS,
    retriever: Retriever | None = None,
) -> list[ScenarioResult]:
    """Score every question run, in each scenario.

    Args:
        questions: The folder of question runs.
        provider: The answering model.
        scenarios: The scenarios to score.
        retriever: The source of retrieved runs, for any scenario that retrieves.

    Returns:
        One result per scenario, in the order given.
    """
    runs = [SavedRun.load(folder) for folder in sorted(questions.iterdir()) if folder.is_dir()]
    results = []
    for scenario in scenarios:
        result = ScenarioResult(scenario.name)
        for run in runs:
            result.outcomes.append(evaluate_question(run, provider, scenario, retriever))
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
        lines.append(f"## {result.name}: {result.right} of {total}")
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


def render_report(
    results: Sequence[ScenarioResult], model: str, title: str = BASELINE_TITLE
) -> str:
    """Write a detailed Markdown report: the pipeline, the scores, and every question's answer.

    Args:
        results: The scenario results.
        model: The model that answered.
        title: The report's heading; defaults to the baseline title, so a report that
            includes a retrieval scenario is not mislabelled as the no-retrieval floor.

    Returns:
        The report, showing for each question the failure the model saw and what it answered.
    """
    total = len(results[0].outcomes) if results else 0
    lines = [
        f"# {title}",
        "",
        f"Model: `{model}`, temperature 0, fixed seed. Scored on whether the model named the "
        "fix that resolves each failure. Counts, not percentages: nine questions.",
        "",
        PIPELINE_DIAGRAM,
        "",
    ]
    for result in results:
        lines += [f"## {result.name}: {result.right} of {total}", ""]
        lines += [
            "| Fault | Expected | Chosen | | Error shown → the model's answer |",
            "|---|---|---|---|---|",
        ]
        for outcome in result.outcomes:
            mark = "✓" if outcome.right else "✗"
            detail = f"`{clip(outcome.error, 90)}` -> {clip(outcome.answer, 130)}"
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


def _cited_precedent_finding(results: Sequence[ScenarioResult]) -> str:
    """Report faults whose matching past failure was cited yet not acted on.

    A history source is named for its fault (``cable_loss-1``), so a wrong answer that cited a
    source of its own fault had the resolving precedent in front of it and still missed.

    Args:
        results: The scenario results.

    Returns:
        The finding sentence, or an empty string when every cited precedent was acted on.
    """
    cited_but_wrong = [
        outcome
        for result in results
        for outcome in result.outcomes
        if not outcome.right
        and any(source.rsplit("-", 1)[0] == outcome.fault for source in outcome.cited)
    ]
    if not cited_but_wrong:
        return ""
    faults = ", ".join(sorted({outcome.fault for outcome in cited_but_wrong}))
    return (
        f"Cited the matching past failure yet still chose wrong: {faults} "
        f"({len(cited_but_wrong)} of the wrong answers). Retrieval surfaced the precedent; "
        "the model did not act on it, so the ceiling here is the model, not retrieval."
    )


def findings(results: Sequence[ScenarioResult]) -> list[str]:
    """Draw conclusions from the scores, computed so they can't drift from the data.

    Args:
        results: The scenario results.

    Returns:
        One sentence per finding: the best scenario, whether more evidence helped, the most
        common wrong choice, any fault whose matching precedent was cited yet not acted on,
        and which faults were solved in every scenario.
    """
    total = len(results[0].outcomes) if results else 0
    conclusions: list[str] = []
    best = max(results, key=lambda result: result.right)
    conclusions.append(f"Best scenario: {best.name}, {best.right} of {total}.")
    progression = "; ".join(f"{result.name} {result.right}" for result in results)
    conclusions.append(f"Scores in order: {progression} (of {total}).")
    if len(results) > 1:
        prior, final = results[-2], results[-1]
        delta = final.right - prior.right
        change = (
            f"{'raised' if delta > 0 else 'lowered'} the score by {abs(delta)}"
            if delta
            else "did not change the score"
        )
        conclusions.append(
            f"The last step ({prior.name} to {final.name}) {change} "
            f"({prior.right} to {final.right} of {total})."
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
    unused_precedent = _cited_precedent_finding(results)
    if unused_precedent:
        conclusions.append(unused_precedent)
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


def main(
    arguments: Sequence[str] | None = None,
    provider: LLMProvider | None = None,
    retriever: Retriever | None = None,
) -> int:
    """Run the evaluation from the command line.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        provider: The answering model; None uses Ollama with the model named by --model.
        retriever: The source of retrieved runs; None builds an in-memory index from the
            history runs with the Ollama embedder when a retrieval scenario is selected.

    Returns:
        0 when it ran, 1 if the model wasn't reachable.
    """
    parser = argparse.ArgumentParser(
        prog="python -m triage.evaluate", description="Score triage against the question runs."
    )
    names = [scenario.name for scenario in SCENARIOS]
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS, help="question runs")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="history runs")
    parser.add_argument(
        "--scenario", choices=names, action="append", help="scenario(s); default: all three"
    )
    parser.add_argument(
        "--report", type=Path, help="also write a detailed Markdown report to this file"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Ollama model to ask (default: {DEFAULT_MODEL})"
    )
    parsed = parser.parse_args(arguments)
    provider = provider or OllamaProvider(model=parsed.model)
    chosen = parsed.scenario or names
    scenarios = [scenario for scenario in SCENARIOS if scenario.name in chosen]
    if retriever is None and any(scenario.retrieve for scenario in scenarios):
        retriever = InMemoryRetriever.from_history(  # pragma: no cover - needs Ollama
            parsed.history, OllamaEmbedder()
        )
    results = evaluate(parsed.questions, provider, scenarios, retriever)
    model = getattr(provider, "model", DEFAULT_MODEL)
    sys.stdout.write(render(results, model))
    if parsed.report is not None:
        parsed.report.parent.mkdir(parents=True, exist_ok=True)
        title = (
            RETRIEVAL_TITLE if any(scenario.retrieve for scenario in scenarios) else BASELINE_TITLE
        )
        parsed.report.write_text(render_report(results, model, title), encoding="utf-8")
        sys.stdout.write(f"\nwrote {parsed.report}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
