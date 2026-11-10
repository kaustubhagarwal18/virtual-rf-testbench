"""Writing up an evaluation: the console summary, the Markdown report and its findings.

Everything here reads scores computed in triage.scores and never asks the model, so a
report can be rewritten from the same results without changing a number.
"""

from collections.abc import Sequence
from dataclasses import replace

from triage.scores import MALFORMED, Outcome, Scenario, ScenarioResult, wilson_interval
from triage.textfmt import clip

BASELINE_TITLE = "Triage baseline (no retrieval)"
"""The report heading when no scenario retrieves: the recorded floor in baseline.md."""

RETRIEVAL_TITLE = "Triage: retrieval against the no-retrieval baseline"
"""The report heading when a retrieval scenario is scored alongside the baseline ones."""


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
        "| Scenario | Changes one thing in | Faults solved | 95% interval | Questions right "
        "| UNKNOWN | Wrong | Malformed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for result in results:
        low, high = wilson_interval(result.faults_solved, result.faults)
        lines.append(
            f"| {result.name} | {result.scenario.adds_to} "
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
        f"{len(among)} are among them: on those, retrieval finds a near-identical past run, "
        "which shows recall, not generalising to a new one.",
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
                f"{result.name}: with no past run of its own fault to go on, "
                f"{_answerer(result.scenario)} said "
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


def _answerer(scenario: Scenario) -> str:
    """Name who answered in a scenario, for a finding's sentence.

    Args:
        scenario: The scenario.

    Returns:
        'the vote' when the precedents' majority vote answered, otherwise 'the model'.
    """
    return "the vote" if scenario.vote else "the model"


def _settings(scenario: Scenario) -> tuple[object, ...]:
    """Return what a scenario does, without its name or the one it says it changes.

    Args:
        scenario: The scenario.

    Returns:
        Its evidence level, precedents, checks, gates and who answers.
    """
    return (scenario.level, scenario.precedents, scenario.checked, scenario.gated, scenario.vote)


def _gate_findings(results: Sequence[ScenarioResult]) -> list[str]:
    """Say how often the gates answered for the model, and what that cost or saved.

    Each gated scenario is compared, question by question, with the scored scenario that
    is the same in every way but the gates: a gated question that scenario got right is a
    right answer given up, and one it got confidently wrong is a wrong answer prevented. The
    neighbour is found by its settings, not by `adds_to`, since a vote scenario names its
    model twin there, which is gated too.

    Args:
        results: The scenario results.

    Returns:
        One sentence per gated scenario whose ungated neighbour was also scored.
    """
    sentences = []
    for result in results:
        if not result.scenario.gated:
            continue
        ungated = _settings(replace(result.scenario, gated=False))
        prior = next((other for other in results if _settings(other.scenario) == ungated), None)
        if prior is None:
            continue
        before = {outcome.question: outcome for outcome in prior.outcomes}
        gated = [outcome for outcome in result.outcomes if outcome.sources.gated]
        given_up = sum(1 for outcome in gated if before[outcome.question].right)
        prevented = sum(1 for outcome in gated if before[outcome.question].confidently_wrong)
        sentences.append(
            f"{result.name}: the gates answered UNKNOWN "
            f"{'before the vote' if result.scenario.vote else 'without asking the model'} on "
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
