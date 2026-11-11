"""Score triage against the question runs: did it name the fix that resolves each failure?

Each scenario adds one thing to a neighbour, so any change in the score is attributable to
that one thing: the error message alone, the whole run's evidence, that evidence without its
firmware line or without its "No signal" line (two ablations), the evidence plus the past
runs most like it (retrieval), the same with the checks on the verdict, the same again with
the question's own fault held out of the history, and both retrieval scenarios with gates
that answer UNKNOWN from the precedents alone. Five more replace the model with a majority
vote over the same precedents (triage.vote), each the twin of a model scenario, to show what
the model adds beyond retrieval.

An answer is one of four things: right, UNKNOWN (the model abstained), a confident wrong
action, or malformed (unusable). They are counted apart, because a confident wrong action is
the failure that matters and UNKNOWN is the honest answer when there is no precedent.

Scores are counts with a 95% interval, not percentages: with a few dozen questions, '18 of
27, roughly 13 to 22' is honest where '67%' is not.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from triage.abstain import DEFAULT_GATES
from triage.answer import VERDICT_SCHEMA, MalformedAnswerError, Verdict
from triage.checks import DEFAULT_CHECKS
from triage.embed import OllamaEmbedder
from triage.evidence import (
    ERROR_ONLY,
    FULL,
    FULL_WITHOUT_FIRMWARE,
    FULL_WITHOUT_SIGNAL,
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
from triage.report import BASELINE_TITLE, RETRIEVAL_TITLE, render, render_report
from triage.retrieval import DEFAULT_HISTORY, DEFAULT_TOP_K, InMemoryRetriever, Retriever
from triage.scores import MALFORMED, Outcome, Precedents, Scenario, ScenarioResult, SourcesUsed
from triage.service import triage_run
from triage.vote import vote_run

DEFAULT_QUESTIONS = Path("triage/data/runs/questions")
DEFAULT_CACHE = Path(".cache/triage-answers")
"""Where the model's answers persist between runs; git ignores it."""

SCENARIOS = (
    Scenario("error only", ERROR_ONLY),
    Scenario("full evidence", FULL, adds_to="error only"),
    Scenario("full evidence, no firmware line", FULL_WITHOUT_FIRMWARE, adds_to="full evidence"),
    Scenario("full evidence, no signal line", FULL_WITHOUT_SIGNAL, adds_to="full evidence"),
    Scenario("full + retrieval", FULL, precedents=Precedents.ALL, adds_to="full evidence"),
    Scenario(
        "full + retrieval + checks",
        FULL,
        precedents=Precedents.ALL,
        checked=True,
        adds_to="full + retrieval",
    ),
    Scenario(
        "full + retrieval + checks, own fault held out",
        FULL,
        precedents=Precedents.OWN_FAULT_HELD_OUT,
        checked=True,
        adds_to="full + retrieval + checks",
    ),
    Scenario(
        "full + retrieval + checks + gates",
        FULL,
        precedents=Precedents.ALL,
        checked=True,
        gated=True,
        adds_to="full + retrieval + checks",
    ),
    Scenario(
        "full + retrieval + checks + gates, own fault held out",
        FULL,
        precedents=Precedents.OWN_FAULT_HELD_OUT,
        checked=True,
        gated=True,
        adds_to="full + retrieval + checks, own fault held out",
    ),
    Scenario(
        "majority vote",
        FULL,
        precedents=Precedents.ALL,
        vote=True,
        adds_to="full + retrieval",
    ),
    Scenario(
        "majority vote + checks",
        FULL,
        precedents=Precedents.ALL,
        checked=True,
        vote=True,
        adds_to="full + retrieval + checks",
    ),
    Scenario(
        "majority vote + checks, own fault held out",
        FULL,
        precedents=Precedents.OWN_FAULT_HELD_OUT,
        checked=True,
        vote=True,
        adds_to="full + retrieval + checks, own fault held out",
    ),
    Scenario(
        "majority vote + checks + gates",
        FULL,
        precedents=Precedents.ALL,
        checked=True,
        gated=True,
        vote=True,
        adds_to="full + retrieval + checks + gates",
    ),
    Scenario(
        "majority vote + checks + gates, own fault held out",
        FULL,
        precedents=Precedents.OWN_FAULT_HELD_OUT,
        checked=True,
        gated=True,
        vote=True,
        adds_to="full + retrieval + checks + gates, own fault held out",
    ),
)
"""The scenarios, each changing exactly one thing in the one it names. A vote scenario
names its model twin: the one thing it changes is the vote answering in the model's place."""


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


def _decide(
    run: SavedRun, provider: LLMProvider, scenario: Scenario, sources: Sequence[Source]
) -> Verdict:
    """Answer one question the scenario's way: by the model, or by the precedents' vote.

    Args:
        run: The question run.
        provider: The answering model, never asked in a vote scenario.
        scenario: The scenario, which says who answers and with which gates and checks.
        sources: The precedents retrieval found for the question.

    Returns:
        The verdict, after the scenario's gates and checks.
    """
    checks = DEFAULT_CHECKS if scenario.checked else ()
    gates = DEFAULT_GATES if scenario.gated else ()
    if scenario.vote:
        return vote_run(run, sources, checks=checks, gates=gates)
    return triage_run(run, provider, scenario.level, sources, checks=checks, gates=gates)


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
    try:
        verdict = _decide(run, provider, scenario, sources)
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
    answer = (
        f"{verdict.fix.name} by majority vote, model not asked: {verdict.because}{overruled}"
        if scenario.vote
        else f"{verdict.fix.name} ({verdict.where}){cited} because {verdict.because}{overruled}"
    )
    return replace(
        base,
        chosen=verdict.fix.name,
        answer=answer,
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
