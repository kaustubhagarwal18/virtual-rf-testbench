"""Score triage against the question runs: did it name the fix that resolves each failure?

Each scenario adds one thing to a neighbour, so any change in the score is attributable to
that one thing: the error message alone, the whole run's evidence, that evidence without its
firmware line (an ablation), the evidence plus the past runs most like it (retrieval), the
same with the checks on the verdict, the same again with the question's own fault held out
of the history, and both retrieval scenarios with gates that answer UNKNOWN from the
precedents alone.

An answer is one of four things: right, UNKNOWN (the model abstained), a confident wrong
action, or malformed (unusable). They are counted apart, because a confident wrong action is
the failure that matters and UNKNOWN is the honest answer when there is no precedent.

Scores are counts with a 95% interval, not percentages: with a few dozen questions, '18 of
27, roughly 13 to 22' is honest where '67%' is not.
"""

import argparse
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from triage.abstain import DEFAULT_GATES
from triage.answer import VERDICT_SCHEMA, MalformedAnswerError
from triage.checks import DEFAULT_CHECKS
from triage.embed import OllamaEmbedder
from triage.evidence import (
    ERROR_ONLY,
    FULL,
    FULL_WITHOUT_FIRMWARE,
    SavedRun,
    error_only_evidence,
)
from triage.label import load_label
from triage.llm import (
    DEFAULT_MODEL,
    CachingProvider,
    LLMProvider,
    ModelUnavailableError,
    OllamaProvider,
)
from triage.prompt import Fix, Source
from triage.retrieval import DEFAULT_HISTORY, DEFAULT_TOP_K, InMemoryRetriever, Retriever
from triage.service import triage_run
from triage.textfmt import clip

DEFAULT_QUESTIONS = Path("triage/data/runs/questions")
DEFAULT_CACHE = Path(".cache/triage-answers")
"""Where the model's answers persist between runs; git ignores it."""
MALFORMED = "malformed"
"""What a reply that couldn't be parsed counts as: neither right nor a real wrong answer."""

BASELINE_TITLE = "Triage baseline (no retrieval)"
"""The report heading when no scenario retrieves: the recorded floor in baseline.md."""

RETRIEVAL_TITLE = "Triage: retrieval against the no-retrieval baseline"
"""The report heading when a retrieval scenario is scored alongside the baseline ones."""

Z_95 = 1.96
"""The normal quantile for a two-sided 95% interval."""


@dataclass(frozen=True)
class Scenario:
    """One way of asking the model, so scenarios can be compared.

    Attributes:
        name: What the scenario is called in the report.
        level: The evidence level the model is given.
        retrieve: Whether the past runs most like this one are offered as sources.
        hold_out: Whether past runs of the question's own fault are kept out of retrieval,
            so the scenario measures what the model does with no precedent of that fault.
            Another fault with the same fix (link_down for link_flaky) may still be offered.
        checked: Whether the verdict goes through the service's checks.
        gated: Whether the precedents go through the abstention gates before the model is
            asked, so a question with no basis for an answer gets UNKNOWN unasked.
        adds_to: The scenario this one changes exactly one thing in; empty for the first.
    """

    name: str
    level: str
    retrieve: bool = False
    hold_out: bool = False
    checked: bool = False
    gated: bool = False
    adds_to: str = ""


SCENARIOS = (
    Scenario("error only", ERROR_ONLY),
    Scenario("full evidence", FULL, adds_to="error only"),
    Scenario("full evidence, no firmware line", FULL_WITHOUT_FIRMWARE, adds_to="full evidence"),
    Scenario("full + retrieval", FULL, retrieve=True, adds_to="full evidence"),
    Scenario(
        "full + retrieval + checks",
        FULL,
        retrieve=True,
        checked=True,
        adds_to="full + retrieval",
    ),
    Scenario(
        "full + retrieval + checks, own fault held out",
        FULL,
        retrieve=True,
        hold_out=True,
        checked=True,
        adds_to="full + retrieval + checks",
    ),
    Scenario(
        "full + retrieval + checks + gates",
        FULL,
        retrieve=True,
        checked=True,
        gated=True,
        adds_to="full + retrieval + checks",
    ),
    Scenario(
        "full + retrieval + checks + gates, own fault held out",
        FULL,
        retrieve=True,
        hold_out=True,
        checked=True,
        gated=True,
        adds_to="full + retrieval + checks, own fault held out",
    ),
)
"""The scenarios, each changing exactly one thing in the one it names."""


@dataclass(frozen=True)
class SourcesUsed:
    """The past runs a question was offered, and which of them the model cited.

    Attributes:
        offered_faults: The labelled fault of each source offered, most similar first;
            empty when the scenario doesn't retrieve.
        cited: The source ids the model cited; empty when it cited none or was unusable.
        cited_faults: The labelled fault of each cited source, in the same order.
        gated: Whether a gate found the offered sources no basis for an answer, so UNKNOWN
            was given and the model wasn't asked.
    """

    offered_faults: tuple[str, ...] = ()
    cited: tuple[str, ...] = ()
    cited_faults: tuple[str, ...] = ()
    gated: bool = False


@dataclass(frozen=True)
class Outcome:
    """One question asked once, in one scenario.

    Attributes:
        question: The question run's folder name.
        fault: The fault the run holds, from its label, which names the expected fix.
        expected: The fix that resolves it.
        chosen: The fix the model chose, or MALFORMED when the reply couldn't be used.
        error: The failure the model was shown, as one line.
        answer: What the model said: its chosen fix, where and why, or the unusable reply.
        sources: The past runs offered, which the model cited, and whether a gate answered.
    """

    question: str
    fault: str
    expected: Fix
    chosen: str
    error: str = ""
    answer: str = ""
    sources: SourcesUsed = SourcesUsed()

    @property
    def right(self) -> bool:
        """Whether the chosen fix is the one that resolves the fault.

        Returns:
            True for the resolving fix.
        """
        return self.chosen == self.expected.name

    @property
    def abstained(self) -> bool:
        """Whether the model said UNKNOWN rather than act.

        Returns:
            True for UNKNOWN, unless UNKNOWN were itself the expected fix.
        """
        return not self.right and self.chosen == Fix.UNKNOWN.name

    @property
    def confidently_wrong(self) -> bool:
        """Whether the model named an action, and it was the wrong one.

        Returns:
            True for a wrong action that isn't UNKNOWN or an unusable reply.
        """
        return not self.right and not self.abstained and self.chosen != MALFORMED

    @property
    def precedent_offered(self) -> bool:
        """Whether retrieval offered at least one past run of the question's own fault.

        Returns:
            True if one of the offered sources has the question's fault.
        """
        return self.fault in self.sources.offered_faults


@dataclass
class ScenarioResult:
    """Every outcome for one scenario.

    Attributes:
        scenario: The scenario that was scored.
        outcomes: One outcome per question asked.
    """

    scenario: Scenario
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def name(self) -> str:
        """The scenario's name, for the report.

        Returns:
            The name.
        """
        return self.scenario.name

    @property
    def total(self) -> int:
        """How many questions were asked.

        Returns:
            The count.
        """
        return len(self.outcomes)

    @property
    def right(self) -> int:
        """How many questions got the resolving fix.

        Returns:
            The count.
        """
        return sum(1 for outcome in self.outcomes if outcome.right)

    @property
    def abstained(self) -> int:
        """How many questions the model answered UNKNOWN.

        Returns:
            The count.
        """
        return sum(1 for outcome in self.outcomes if outcome.abstained)

    @property
    def malformed(self) -> int:
        """How many replies couldn't be used.

        Returns:
            The count.
        """
        return sum(1 for outcome in self.outcomes if outcome.chosen == MALFORMED)

    @property
    def confidently_wrong(self) -> int:
        """How many questions got a wrong action that wasn't UNKNOWN.

        Returns:
            The count.
        """
        return sum(1 for outcome in self.outcomes if outcome.confidently_wrong)

    @property
    def precedent_offered(self) -> int:
        """How many questions retrieval offered a past run of their own fault for.

        Returns:
            The count; zero for a scenario that doesn't retrieve.
        """
        return sum(1 for outcome in self.outcomes if outcome.precedent_offered)

    @property
    def by_fault(self) -> dict[str, tuple[int, int]]:
        """Count each fault's questions: how many were right, of how many asked.

        Returns:
            (right, asked) for each fault, in name order.
        """
        counts: dict[str, tuple[int, int]] = {}
        for outcome in self.outcomes:
            right, asked = counts.get(outcome.fault, (0, 0))
            counts[outcome.fault] = (right + outcome.right, asked + 1)
        return dict(sorted(counts.items()))

    @property
    def faults(self) -> int:
        """How many faults were asked about.

        Returns:
            The count.
        """
        return len(self.by_fault)

    @property
    def faults_solved(self) -> int:
        """How many faults had every one of their questions right.

        Returns:
            The count.
        """
        return sum(1 for right, asked in self.by_fault.values() if right == asked)

    @property
    def faults_split(self) -> int:
        """How many faults had some questions right and some wrong.

        Returns:
            The count; zero means a fault's questions always behave as one.
        """
        return sum(1 for right, asked in self.by_fault.values() if 0 < right < asked)

    @property
    def wrong_choices(self) -> dict[str, int]:
        """What the model chose when it named a wrong action, counted.

        Returns:
            Each confident wrong choice and how often it was made, most frequent first.
        """
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            if outcome.confidently_wrong:
                counts[outcome.chosen] = counts.get(outcome.chosen, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: -item[1]))


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    """Return the 95% Wilson score interval for a count, as counts rather than a fraction.

    The Wilson interval stays inside 0..total and behaves at small counts and at the
    extremes, where the textbook normal interval does not.

    Args:
        successes: How many were right.
        total: How many were asked.

    Returns:
        The interval's low and high ends, in the unit counted; (0, 0) when nothing was asked.
    """
    if total == 0:
        return 0.0, 0.0
    proportion = successes / total
    spread = Z_95**2 / total
    centre = (proportion + spread / 2) / (1 + spread)
    half_width = (
        Z_95
        * math.sqrt(proportion * (1 - proportion) / total + Z_95**2 / (4 * total**2))
        / (1 + spread)
    )
    # At none or all right, that end is exactly 0 or total; floating point misses by 1e-16.
    low = 0.0 if successes == 0 else (centre - half_width) * total
    high = float(total) if successes == total else (centre + half_width) * total
    return low, high


def score_line(result: ScenarioResult) -> str:
    """Summarise one scenario: faults solved with their interval, then the question counts.

    The fault is the unit scored. A fault's questions differ only in size, timing and
    reading noise, and in practice they are answered alike, so 27 questions carry about as
    much evidence as 9 faults; an interval over the questions would be too narrow.

    Args:
        result: The scenario's result.

    Returns:
        A line such as '6 of 9 faults solved (95% interval 3 to 8); 18 of 27 questions right;
        0 UNKNOWN, 9 wrong, 0 malformed'.
    """
    low, high = wilson_interval(result.faults_solved, result.faults)
    return (
        f"{result.faults_solved} of {result.faults} faults solved (95% interval {low:.0f} to "
        f"{high:.0f}); {result.right} of {result.total} questions right; "
        f"{result.abstained} UNKNOWN, {result.confidently_wrong} wrong, "
        f"{result.malformed} malformed"
    )


def expected_fix(run: SavedRun) -> Fix:
    """Return the fix that resolves a question run, from the action its label records.

    The label is read here, and only here: it is the answer key, used to score the model's
    verdict, never shown to the model. The action comes from the fault catalogue through
    `make fault`, so there is one answer key, not a second table to keep in step.

    Args:
        run: The question run.

    Returns:
        The resolving fix.

    Raises:
        KeyError: If the label records no action, or one that isn't on the list; the
            message says to rebuild the data set.
    """
    action = load_label(run).get("action")
    if not isinstance(action, str) or action not in Fix.__members__:
        raise KeyError(
            f"{run.folder.name}: the label records no known action ({action!r}); rebuild "
            "the data set with 'make fault-dataset'"
        )
    return Fix[action]


def _sources_for(
    run: SavedRun, fault: str, scenario: Scenario, retriever: Retriever | None
) -> tuple[Source, ...]:
    """Find the past runs a scenario offers for one question.

    Args:
        run: The question run.
        fault: Its labelled fault, held out of retrieval when the scenario says so.
        scenario: The scenario.
        retriever: Where retrieved sources come from; required when the scenario retrieves.

    Returns:
        The sources to offer; none when the scenario doesn't retrieve.

    Raises:
        ValueError: If the scenario retrieves but no retriever was given.
    """
    if not scenario.retrieve:
        return ()
    if retriever is None:
        raise ValueError(f"scenario {scenario.name!r} retrieves but no retriever was given")
    excluded = fault if scenario.hold_out else None
    return tuple(retriever.similar_to(run, DEFAULT_TOP_K, exclude_fault=excluded))


def evaluate_question(
    run: SavedRun, provider: LLMProvider, scenario: Scenario, retriever: Retriever | None
) -> Outcome:
    """Ask about one run in one scenario, and score the answer.

    Args:
        run: The question run.
        provider: The answering model.
        scenario: How to ask: the evidence level, retrieval, hold-out and checks.
        retriever: Where retrieved sources come from; required when the scenario retrieves.

    Returns:
        The outcome, which keeps the failure the model was shown and what it answered, so a
        report can show both; a reply that couldn't be parsed counts as MALFORMED, not right.
    """
    expected = expected_fix(run)
    fault = str(load_label(run)["fault"])
    sources = _sources_for(run, fault, scenario, retriever)
    base = Outcome(
        question=run.folder.name,
        fault=fault,
        expected=expected,
        chosen=MALFORMED,
        error=error_only_evidence(run),
        sources=SourcesUsed(offered_faults=tuple(source.fault for source in sources)),
    )
    checks = DEFAULT_CHECKS if scenario.checked else ()
    gates = DEFAULT_GATES if scenario.gated else ()
    try:
        verdict = triage_run(run, provider, scenario.level, sources, checks=checks, gates=gates)
    except MalformedAnswerError as unusable:
        return replace(base, answer=f"unusable: {unusable}")
    if verdict.gate_reason:
        return replace(
            base,
            chosen=verdict.fix.name,
            answer=f"UNKNOWN, model not asked: {verdict.gate_reason}",
            sources=replace(base.sources, gated=True),
        )
    fault_by_id = {source.source_id: source.fault for source in sources}
    cited = f" citing {', '.join(verdict.cited)}" if verdict.cited else ""
    overruled = f" [overruled: {verdict.overruled}]" if verdict.overruled else ""
    return replace(
        base,
        chosen=verdict.fix.name,
        answer=f"{verdict.fix.name} ({verdict.where}){cited} because {verdict.because}{overruled}",
        sources=replace(
            base.sources,
            cited=tuple(verdict.cited),
            cited_faults=tuple(fault_by_id[source_id] for source_id in verdict.cited),
        ),
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
        result = ScenarioResult(scenario)
        for run in runs:
            result.outcomes.append(evaluate_question(run, provider, scenario, retriever))
        results.append(result)
    return results


def _tally(choices: dict[str, int]) -> str:
    """Format a count of choices as 'FIRMWARE x4, CABLE x2'.

    Args:
        choices: Each choice and its count.

    Returns:
        The formatted tally.
    """
    return ", ".join(f"{choice} x{count}" for choice, count in choices.items())


def _mark(outcome: Outcome) -> str:
    """Mark one outcome for the report.

    Args:
        outcome: The outcome.

    Returns:
        ✓ for right, ? for UNKNOWN, ! for malformed, ✗ for a confident wrong action.
    """
    if outcome.right:
        return "✓"
    if outcome.abstained:
        return "?"
    return "!" if outcome.chosen == MALFORMED else "✗"


def render(results: Sequence[ScenarioResult], model: str) -> str:
    """Write the scores as text, a scenario at a time.

    Args:
        results: The scenario results.
        model: The model that answered, named in the heading.

    Returns:
        The report.
    """
    lines = [f"Triage evaluation ({model}): did it name the fix that resolves the failure?", ""]
    for result in results:
        lines.append(f"## {result.name}: {score_line(result)}")
        for outcome in result.outcomes:
            mark = "ok  " if outcome.right else "MISS"
            lines.append(
                f"  {mark} {outcome.question:34} expected {outcome.expected.name:13} "
                f"chose {outcome.chosen}"
            )
        if result.wrong_choices:
            lines.append(f"  wrong choices: {_tally(result.wrong_choices)}")
        lines.append("")
    return "\n".join(lines)


PIPELINE_DIAGRAM = """```mermaid
flowchart LR
    run["Saved run<br/>results, logs, sequence"] --> ev["evidence.py<br/>first failure"]
    hist[("History runs<br/>neutral ids")] --> ret["retrieval.py<br/>top 3"]
    ev --> pr["prompt.py<br/>instructions + fix list"]
    ret --> gate{"abstain.py<br/>precedents agree<br/>and close?"}
    gate -- yes --> pr
    gate -- no --> unk["UNKNOWN<br/>model not asked"]
    pr --> llm["qwen2.5:3b<br/>(Ollama, local, schema)"]
    llm --> an["answer.py<br/>parse + refuse unusable"]
    an --> chk["checks.py<br/>release the run ran?<br/>firmware unchanged?<br/>SCPI error?"]
    chk --> score["evaluate.py<br/>right fix?"]
    label["label.json"] -. answer key, never shown .-> score
```"""
"""The pipeline, drawn once for the report. The label reaches only the scoring, never qwen."""


def _report_rows(result: ScenarioResult) -> list[str]:
    """Write one scenario's table: each question, what it saw and what it answered.

    Args:
        result: The scenario's result.

    Returns:
        The table's lines, ending with the wrong-choice tally when there is one.
    """
    retrieves = result.scenario.retrieve
    precedent_header = " Precedent offered |" if retrieves else ""
    lines = [
        f"| Question | Expected | Chosen | |{precedent_header} Error shown → the model's answer |",
        f"|---|---|---|---|{'---|' if retrieves else ''}---|",
    ]
    for outcome in result.outcomes:
        precedent = f" {'yes' if outcome.precedent_offered else 'no'} |" if retrieves else ""
        detail = f"`{clip(outcome.error, 90)}` -> {clip(outcome.answer, 130)}"
        lines.append(
            f"| {outcome.question} | {outcome.expected.name} | {outcome.chosen} | "
            f"{_mark(outcome)} |{precedent} {detail} |"
        )
    if result.wrong_choices:
        lines += ["", f"Confident wrong choices: {_tally(result.wrong_choices)}."]
    return lines


def _summary_table(results: Sequence[ScenarioResult]) -> list[str]:
    """Write the scenarios side by side: faults solved first, then the question counts.

    Args:
        results: The scenario results.

    Returns:
        The table's lines.
    """
    lines = [
        "| Scenario | Adds | Faults solved | 95% interval | Questions right | UNKNOWN | Wrong "
        "| Malformed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for result in results:
        low, high = wilson_interval(result.faults_solved, result.faults)
        lines.append(
            f"| {result.name} | {result.scenario.adds_to and 'to ' + result.scenario.adds_to} "
            f"| {result.faults_solved} of {result.faults} | {low:.0f} to {high:.0f} "
            f"| {result.right} of {result.total} | {result.abstained} "
            f"| {result.confidently_wrong} | {result.malformed} |"
        )
    return lines


def _fault_table(results: Sequence[ScenarioResult]) -> list[str]:
    """Write each fault's questions right, scenario by scenario.

    Args:
        results: The scenario results.

    Returns:
        The table's lines: one row per fault, one column per scenario.
    """
    header = " | ".join(result.name for result in results)
    lines = [f"| Fault | Expected | {header} |", "|---|---|" + "---|" * len(results)]
    expected = {outcome.fault: outcome.expected.name for outcome in results[0].outcomes}
    for fault in results[0].by_fault:
        counts = [result.by_fault.get(fault, (0, 0)) for result in results]
        cells = " | ".join(f"{right}/{asked}" for right, asked in counts)
        lines.append(f"| {fault} | {expected[fault]} | {cells} |")
    return lines


def repeated_faults(questions: Path) -> list[str]:
    """Name the faults whose question runs repeat their history runs' scenario.

    A fault with a size (dB of cable loss, seconds of delay, sweeps between drops) is asked
    at sizes no history run has. A fault without one, whose label records no unit, can only
    be asked again as it was recorded: its questions differ from its history runs in timing
    and reading noise alone, so retrieval solving them shows recall, not generalisation.

    Args:
        questions: The folder of question runs.

    Returns:
        The faults whose labels record no unit, in name order.
    """
    labels = [
        load_label(SavedRun.load(folder))
        for folder in sorted(questions.iterdir())
        if folder.is_dir()
    ]
    return sorted({str(label["fault"]) for label in labels if not label.get("unit")})


def _repeat_caveat(results: Sequence[ScenarioResult], repeated: Sequence[str]) -> list[str]:
    """Say which faults repeat a history scenario, and how much of the retrieval score they are.

    Args:
        results: The scenario results.
        repeated: The faults whose questions repeat their history runs' scenario.

    Returns:
        The caveat's lines; none when no fault repeats or no scenario retrieves, since
        without retrieval a repeated scenario gives the model nothing to recall.
    """
    retrieving = [result for result in results if result.scenario.retrieve]
    if not repeated or not retrieving:
        return []
    best = max(retrieving, key=lambda result: (result.faults_solved, result.right))
    solved = [fault for fault, (right, asked) in best.by_fault.items() if right == asked]
    among = [fault for fault in solved if fault in repeated]
    return [
        "",
        f"{len(repeated)} faults have no size to vary ({', '.join(repeated)}), so their "
        "questions repeat their history runs' scenario and differ only in timing and reading "
        f"noise (see triage/data/README.md). Of the {len(solved)} faults {best.name} solved, "
        f"{len(among)} are among them: with retrieval, those show the model finding a "
        "near-identical past run, not generalising to a new one.",
    ]


def render_report(
    results: Sequence[ScenarioResult],
    model: str,
    title: str = BASELINE_TITLE,
    repeated: Sequence[str] = (),
) -> str:
    """Write a detailed Markdown report: the pipeline, the scores, and every question's answer.

    Args:
        results: The scenario results.
        model: The model that answered.
        title: The report's heading; defaults to the baseline title, so a report that
            includes a retrieval scenario is not mislabelled as the no-retrieval floor.
        repeated: The faults whose questions repeat their history runs' scenario, which the
            findings must name; none leaves the caveat out.

    Returns:
        The report, showing for each question the failure the model saw and what it answered.
    """
    total = results[0].total if results else 0
    faults = results[0].faults if results else 0
    lines = [
        f"# {title}",
        "",
        f"Model: `{model}`, temperature 0, fixed seed, replies constrained to the answer's "
        f"JSON schema. Scored on whether the model named the fix that resolves each failure: "
        f"{total} questions, {total // faults if faults else 0} for each of {faults} faults. "
        "A fault is solved when every one of its questions is right, and the interval is over "
        "faults, because a fault's questions are answered alike (see Findings). "
        "✓ right, ? UNKNOWN, ✗ a confident wrong action, ! unusable reply.",
        "",
        PIPELINE_DIAGRAM,
        "",
        *_summary_table(results),
        "",
        "## By fault",
        "",
        "Questions right, of those asked, for each fault.",
        "",
        *(_fault_table(results) if results else []),
        "",
    ]
    for result in results:
        lines += [f"## {result.name}: {score_line(result)}", "", *_report_rows(result), ""]
    lines += ["## Findings", ""]
    lines += [f"- {finding}" for finding in findings(results)]
    lines += _repeat_caveat(results, repeated)
    lines += [
        "",
        "These are one machine's deterministic numbers (temperature 0, fixed seed); another "
        "machine's model may differ in wording and the odd verdict. A verdict whose reasoning "
        "is sound but whose action is wrong still counts as a miss.",
    ]
    return "\n".join(lines) + "\n"


def _step_findings(results: Sequence[ScenarioResult]) -> list[str]:
    """Say what each scenario's one added thing did to the score.

    Args:
        results: The scenario results.

    Returns:
        One sentence per scenario that names the one it adds to, when both were scored.
    """
    by_name = {result.name: result for result in results}
    sentences = []
    for result in results:
        prior = by_name.get(result.scenario.adds_to)
        if prior is None:
            continue
        delta = result.faults_solved - prior.faults_solved
        change = (
            f"{'raised' if delta > 0 else 'lowered'} faults solved by {abs(delta)}"
            if delta
            else "did not change the faults solved"
        )
        sentences.append(
            f"{prior.name} to {result.name} {change} ({prior.faults_solved} to "
            f"{result.faults_solved} of {result.faults}); confident wrong answers went from "
            f"{prior.confidently_wrong} to {result.confidently_wrong} of {result.total}."
        )
    return sentences


def _agreement_finding(results: Sequence[ScenarioResult]) -> str:
    """Say how often a fault's questions disagreed, which is why the fault is the unit scored.

    Args:
        results: The scenario results.

    Returns:
        One sentence counting the fault-and-scenario cells whose questions split.
    """
    cells = sum(result.faults for result in results)
    split = sum(result.faults_split for result in results)
    return (
        f"A fault's questions split (some right, some wrong) in {split} of {cells} "
        "fault-and-scenario cells; otherwise they were answered alike, so the fault, not the "
        "question, is the unit the scores count."
    )


def _retrieval_findings(results: Sequence[ScenarioResult]) -> list[str]:
    """Say how often retrieval found a precedent, and what the model did with one or without.

    Args:
        results: The scenario results.

    Returns:
        Sentences on retrieval recall, cited-but-unused precedents and the hold-out scenario.
    """
    sentences = []
    for result in results:
        if not result.scenario.retrieve:
            continue
        if result.scenario.hold_out:
            sentences.append(
                f"{result.name}: with no past run of its own fault to go on, the model said "
                f"UNKNOWN {result.abstained} times and named a wrong action "
                f"{result.confidently_wrong} times, of {result.total}."
            )
            continue
        sentences.append(
            f"{result.name}: retrieval offered a past run of the question's own fault for "
            f"{result.precedent_offered} of {result.total} questions."
        )
        unused = [
            outcome
            for outcome in result.outcomes
            if not outcome.right and outcome.fault in outcome.sources.cited_faults
        ]
        if unused:
            faults = ", ".join(sorted({outcome.fault for outcome in unused}))
            sentences.append(
                f"{result.name}: cited the matching past failure yet still chose wrong on "
                f"{len(unused)} question{'s' if len(unused) != 1 else ''} ({faults})."
            )
    return sentences


def _gate_findings(results: Sequence[ScenarioResult]) -> list[str]:
    """Say how often the gates answered for the model, and what that cost or saved.

    Each gated scenario is compared, question by question, with the scenario it adds the
    gates to: a gated question that scenario got right is a right answer given up, and one
    it got confidently wrong is a wrong answer prevented.

    Args:
        results: The scenario results.

    Returns:
        One sentence per gated scenario whose neighbour was also scored.
    """
    by_name = {result.name: result for result in results}
    sentences = []
    for result in results:
        prior = by_name.get(result.scenario.adds_to)
        if not result.scenario.gated or prior is None:
            continue
        before = {outcome.question: outcome for outcome in prior.outcomes}
        gated = [outcome for outcome in result.outcomes if outcome.sources.gated]
        given_up = sum(1 for outcome in gated if before[outcome.question].right)
        prevented = sum(1 for outcome in gated if before[outcome.question].confidently_wrong)
        sentences.append(
            f"{result.name}: the gates answered UNKNOWN without asking the model on "
            f"{len(gated)} of {result.total} questions; without the gates, {prevented} of "
            f"those were confident wrong answers and {given_up} were right."
        )
    return sentences


def findings(results: Sequence[ScenarioResult]) -> list[str]:
    """Draw conclusions from the scores, computed so they can't drift from the data.

    Args:
        results: The scenario results.

    Returns:
        One sentence per finding: the best scenario, how often a fault's questions split,
        what each added step did, retrieval recall and the hold-out result, what the gates
        gave up and prevented, the most common confident wrong choice, and which faults
        were answered right in every question of every scenario.
    """
    if not results:
        return []
    best = max(results, key=lambda result: (result.faults_solved, result.right))
    conclusions = [f"Best scenario: {best.name}, {score_line(best)}."]
    conclusions.append(_agreement_finding(results))
    conclusions += _step_findings(results)
    conclusions += _retrieval_findings(results)
    conclusions += _gate_findings(results)
    wrong: dict[str, int] = {}
    for result in results:
        for choice, count in result.wrong_choices.items():
            wrong[choice] = wrong.get(choice, 0) + count
    if wrong:
        choice, count = max(wrong.items(), key=lambda item: item[1])
        conclusions.append(
            f"Most common confident wrong choice: {choice} ({count} times across the scenarios)."
        )
    faults = {outcome.fault for outcome in results[0].outcomes}
    missed = {
        outcome.fault for result in results for outcome in result.outcomes if not outcome.right
    }
    always_right = ", ".join(sorted(faults - missed)) or "none"
    conclusions.append(f"Right in every question of every scenario: {always_right}.")
    return conclusions


def main(
    arguments: Sequence[str] | None = None,
    provider: LLMProvider | None = None,
    retriever: Retriever | None = None,
) -> int:
    """Run the evaluation from the command line.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        provider: The answering model; None uses Ollama with the model named by --model,
            constrained to the answer's schema, and asks each distinct prompt once.
        retriever: The source of retrieved runs; None builds an in-memory index from the
            history runs with the Ollama embedder when a retrieval scenario is selected.

    Returns:
        0 when it ran, 1 if a model wasn't reachable.
    """
    parser = argparse.ArgumentParser(
        prog="python -m triage.evaluate", description="Score triage against the question runs."
    )
    names = [scenario.name for scenario in SCENARIOS]
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS, help="question runs")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="history runs")
    parser.add_argument(
        "--scenario", choices=names, action="append", help="scenario(s); default: all of them"
    )
    parser.add_argument(
        "--report", type=Path, help="also write a detailed Markdown report to this file"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Ollama model to ask (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE,
        help=f"where the model's answers persist between runs (default: {DEFAULT_CACHE})",
    )
    parser.add_argument("--no-cache", action="store_true", help="ask the model every prompt again")
    parsed = parser.parse_args(arguments)
    provider = provider or CachingProvider(
        OllamaProvider(model=parsed.model, response_format=VERDICT_SCHEMA),
        None if parsed.no_cache else parsed.cache_dir,
    )
    chosen = parsed.scenario or names
    scenarios = [scenario for scenario in SCENARIOS if scenario.name in chosen]
    asked_before = provider.asked if isinstance(provider, CachingProvider) else 0
    try:
        if retriever is None and any(scenario.retrieve for scenario in scenarios):
            retriever = InMemoryRetriever.from_history(  # pragma: no cover - needs Ollama
                parsed.history, OllamaEmbedder()
            )
        results = evaluate(parsed.questions, provider, scenarios, retriever)
    except ModelUnavailableError as error:
        sys.stdout.write(f"{error}\n")
        return 1
    model = str(getattr(provider, "model", DEFAULT_MODEL))
    sys.stdout.write(render(results, model))
    if isinstance(provider, CachingProvider):
        sys.stdout.write(f"\nasked the model {provider.asked - asked_before} new prompts\n")
    if parsed.report is not None:
        parsed.report.parent.mkdir(parents=True, exist_ok=True)
        title = (
            RETRIEVAL_TITLE if any(scenario.retrieve for scenario in scenarios) else BASELINE_TITLE
        )
        report = render_report(results, model, title, repeated_faults(parsed.questions))
        parsed.report.write_text(report, encoding="utf-8")
        sys.stdout.write(f"\nwrote {parsed.report}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
