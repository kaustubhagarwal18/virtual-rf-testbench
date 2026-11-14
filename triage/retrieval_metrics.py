"""Measure retrieval on its own: did the index offer a run of the question's fault?

The classification evaluation (triage.evaluate) scores the whole pipeline, so a retrieval
miss the model papers over counts as a success there. This module asks the narrower
question first: given a question run's symptoms, does the index return a relevant past run,
and at what rank? Recall@K and MRR here are independent of any model.

Relevance is a rule passed in, never assumed. The only rule this module ships is
`same_fault` - a source is relevant when it records the question's own fault - because that
is the one claim the labels support. Retrieving a *different* fault whose fix might still
help is reported as an observation (which faults came back, in rank order), not scored: a
shared action key is not proof that two incidents are related.

Held-out scenarios exclude faults per query with the retriever's `exclude_faults` filter.
For exact cosine search this is identical to building a separate index per query: the
scores of the remaining sources do not depend on the excluded rows, so filtering the ranked
list keeps exactly the order a smaller index would produce, without re-embedding anything.

The repeated-family flag is a heuristic, not proof: a fault whose label records no unit has
no size to vary, so its question runs can only repeat a history scenario, differing in
timing and reading noise. Exact recall on those families measures recall of near-identical
runs, not generalisation, and the report says so rather than averaging it away.
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from triage.bench_run import load_case
from triage.case import CaseError
from triage.embed import Embedder, OllamaEmbedder
from triage.label import load_label
from triage.llm import ModelUnavailableError
from triage.retrieval import DEFAULT_HISTORY, InMemoryRetriever

DEFAULT_QUESTIONS = Path("triage/data/runs/questions")
DEFAULT_COUNT = 5
"""How many sources to retrieve per query: enough to score Recall@5."""

RECALL_DEPTHS = (1, 3, 5)
"""The K values the report states Recall at, where the retrieved count allows."""

ALL_HISTORY = "all history"
"""Every history run is in the index: the question's own fault has precedents."""

OWN_FAULT_HELD_OUT = "own fault held out"
"""Every run of the question's fault is excluded: exact recall is zero by construction,
and the report shows what retrieval offers instead."""

CATEGORY_HELD_OUT = "category held out"
"""Every fault in the question's category is excluded: only cross-category knowledge
remains, as for a failure whose whole family is new."""

SCENARIOS = (ALL_HISTORY, OWN_FAULT_HELD_OUT, CATEGORY_HELD_OUT)
"""The scenarios the benchmark runs, from easiest to hardest."""

RelevanceRule = Callable[[str, str], bool]
"""Says whether a retrieved source's fault is relevant to a question's fault."""


def same_fault(question_fault: str, source_fault: str) -> bool:
    """Count a source as relevant only when it records the question's own fault.

    Args:
        question_fault: The question run's labelled fault.
        source_fault: The retrieved source's labelled fault.

    Returns:
        True when they are the same fault.
    """
    return question_fault == source_fault


@dataclass(frozen=True)
class QueryResult:
    """What retrieval returned for one question run, kept per question, never merged.

    Attributes:
        question: The question run's folder name, unique in the question set.
        fault: Its labelled fault.
        category: Its labelled category, such as bench, firmware or test.
        repeated_family: True when the fault's label records no unit, so this question can
            only repeat a history scenario (a heuristic for near-duplication, not proof).
        relevant_in_index: How many sources of this fault the index held for this query,
            after the scenario's exclusions; the denominator behind a hit or a miss.
        retrieved_faults: The faults of the retrieved sources, most similar first.
        retrieved_ids: The retrieved sources' neutral ids, in the same order.
    """

    question: str
    fault: str
    category: str
    repeated_family: bool
    relevant_in_index: int
    retrieved_faults: tuple[str, ...]
    retrieved_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioReport:
    """One scenario's raw results: every query kept, metrics computed from them later.

    Attributes:
        scenario: Which scenario ran, one of SCENARIOS.
        count: How many sources were retrieved per query.
        history_sources: How many distinct sources the index held before any exclusion.
        queries: One result per question run, in folder order.
    """

    scenario: str
    count: int
    history_sources: int
    queries: tuple[QueryResult, ...]


def first_relevant_rank(result: QueryResult, relevant: RelevanceRule = same_fault) -> int | None:
    """Find the 1-indexed rank of the first relevant retrieved source.

    Args:
        result: One query's retrieval.
        relevant: The relevance rule.

    Returns:
        The rank, or None when nothing retrieved was relevant.
    """
    for rank, fault in enumerate(result.retrieved_faults, start=1):
        if relevant(result.fault, fault):
            return rank
    return None


def hits_at_k(results: Sequence[QueryResult], k: int, relevant: RelevanceRule = same_fault) -> int:
    """Count the queries with a relevant source in the top k.

    Args:
        results: One result per query.
        k: How deep to look.
        relevant: The relevance rule.

    Returns:
        The count of hits.
    """
    return sum(1 for result in results if (first_relevant_rank(result, relevant) or k + 1) <= k)


def recall_at_k(
    results: Sequence[QueryResult], k: int, relevant: RelevanceRule = same_fault
) -> float:
    """Compute the fraction of queries with a relevant source in the top k.

    Args:
        results: One result per query.
        k: How deep to look.
        relevant: The relevance rule.

    Returns:
        The fraction, between 0 and 1.

    Raises:
        ValueError: If there are no results, because a recall over nothing says nothing.
    """
    if not results:
        raise ValueError("recall over no queries is undefined; pass at least one result")
    return hits_at_k(results, k, relevant) / len(results)


def mean_reciprocal_rank(
    results: Sequence[QueryResult], relevant: RelevanceRule = same_fault
) -> float:
    """Compute the mean of 1/rank of the first relevant source, with a miss counting 0.

    Args:
        results: One result per query.
        relevant: The relevance rule.

    Returns:
        The mean reciprocal rank over the retrieved depth, between 0 and 1.

    Raises:
        ValueError: If there are no results, because a mean over nothing says nothing.
    """
    if not results:
        raise ValueError("MRR over no queries is undefined; pass at least one result")
    reciprocals = []
    for result in results:
        rank = first_relevant_rank(result, relevant)
        reciprocals.append(0.0 if rank is None else 1.0 / rank)
    return sum(reciprocals) / len(reciprocals)


def per_fault_hits(
    results: Sequence[QueryResult], k: int, relevant: RelevanceRule = same_fault
) -> dict[str, tuple[int, int]]:
    """Count hits and questions per fault, aggregated apart from the per-query results.

    Args:
        results: One result per query.
        k: How deep a hit may rank.
        relevant: The relevance rule.

    Returns:
        Each fault mapped to (queries with a relevant source in the top k, queries asked),
        in fault name order.
    """
    counts: dict[str, tuple[int, int]] = {}
    for result in results:
        hit = hits_at_k([result], k, relevant)
        hits, asked = counts.get(result.fault, (0, 0))
        counts[result.fault] = (hits + hit, asked + 1)
    return dict(sorted(counts.items()))


def fault_categories(history: Path) -> dict[str, str]:
    """Map each fault in the history to its labelled category.

    Args:
        history: The folder of labelled history runs.

    Returns:
        Each fault's category, from its runs' labels.

    Raises:
        ValueError: If two runs of one fault record different categories, because a
            category hold-out built on conflicting labels would exclude the wrong runs;
            the message says which fault to relabel.
    """
    categories: dict[str, str] = {}
    for folder in sorted(history.iterdir()):
        if not folder.is_dir():
            continue
        label = load_label(load_case(folder))
        fault = str(label.get("fault", ""))
        category = str(label.get("category", ""))
        if fault in categories and categories[fault] != category:
            raise ValueError(
                f"fault {fault!r} is labelled {categories[fault]!r} and {category!r}; "
                "relabel its history runs so one fault has one category"
            )
        categories[fault] = category
    return categories


def excluded_faults(
    fault: str, category: str, scenario: str, categories: dict[str, str]
) -> frozenset[str]:
    """Name the faults one query's scenario holds out of the index.

    Args:
        fault: The question run's labelled fault.
        category: Its labelled category.
        scenario: One of SCENARIOS.
        categories: Each history fault's category, from fault_categories.

    Returns:
        The faults to exclude for this query: none, the question's own fault, or every
        fault in its category (its own included, even if the history never saw it).

    Raises:
        ValueError: If the scenario isn't one of SCENARIOS, or the category hold-out is
            asked for a question whose label records no category.
    """
    if scenario == ALL_HISTORY:
        return frozenset()
    if scenario == OWN_FAULT_HELD_OUT:
        return frozenset({fault})
    if scenario == CATEGORY_HELD_OUT:
        if not category:
            raise ValueError(
                f"question fault {fault!r} has no category in its label; add one before "
                "running the category hold-out"
            )
        same_category = {name for name, cat in categories.items() if cat == category}
        return frozenset(same_category | {fault})
    raise ValueError(f"unknown scenario {scenario!r}; expected one of {SCENARIOS}")


def evaluate_retrieval(
    questions: Path,
    retriever: InMemoryRetriever,
    scenario: str,
    categories: dict[str, str],
    count: int = DEFAULT_COUNT,
) -> ScenarioReport:
    """Run one scenario over every question, keeping each query's own result.

    Args:
        questions: The folder of question runs.
        retriever: The index over the history runs.
        scenario: One of SCENARIOS.
        categories: Each history fault's category, for the category hold-out.
        count: How many sources to retrieve per query.

    Returns:
        The scenario's report, one QueryResult per question run.
    """
    results = []
    for folder in sorted(questions.iterdir()):
        if not folder.is_dir():
            continue
        run = load_case(folder)
        label = load_label(run)
        fault = str(label.get("fault", ""))
        category = str(label.get("category", ""))
        excluded = excluded_faults(fault, category, scenario, categories)
        retrieved = retriever.similar_to(run, count, exclude_faults=excluded)
        results.append(
            QueryResult(
                question=folder.name,
                fault=fault,
                category=category,
                repeated_family=not label.get("unit"),
                relevant_in_index=sum(
                    1
                    for source in retriever.sources
                    if source.fault == fault and source.fault not in excluded
                ),
                retrieved_faults=tuple(source.fault for source in retrieved),
                retrieved_ids=tuple(source.source_id for source in retrieved),
            )
        )
    return ScenarioReport(scenario, count, len(retriever.sources), tuple(results))


def _fraction(hits: int, total: int) -> str:
    """Show a count as both the fraction and the decimal, so the denominator is visible.

    Args:
        hits: The numerator.
        total: The denominator.

    Returns:
        Text such as '36/39 = 0.92'.
    """
    return f"{hits}/{total} = {hits / total:.2f}"


def _metric_lines(results: Sequence[QueryResult], count: int, heading: str) -> list[str]:
    """Render one group's Recall@K and MRR lines.

    Args:
        results: The group's query results.
        count: How many sources each query retrieved.
        heading: What to call the group.

    Returns:
        The lines; a note instead when the group is empty.
    """
    if not results:
        return [f"{heading}: no questions in this group."]
    depths = [depth for depth in RECALL_DEPTHS if depth <= count]
    recalls = "   ".join(
        f"Recall@{depth} {_fraction(hits_at_k(results, depth), len(results))}" for depth in depths
    )
    return [
        f"{heading} ({len(results)} queries):",
        f"  {recalls}   MRR@{count} {mean_reciprocal_rank(results):.2f}",
    ]


def _per_fault_lines(results: Sequence[QueryResult]) -> list[str]:
    """Render the per-fault table: hits at 1 and 3 of the questions asked, per fault.

    Args:
        results: The scenario's query results.

    Returns:
        One heading and one line per fault, in fault name order.
    """
    lines = ["Per fault (hits@1 / hits@3, of questions; relevant sources in index):"]
    hits_1 = per_fault_hits(results, 1)
    by_fault = {result.fault: result for result in results}
    for fault, (hit3, asked) in per_fault_hits(results, 3).items():
        sample = by_fault[fault]
        flag = "  [repeated family]" if sample.repeated_family else ""
        lines.append(
            f"  {fault} ({sample.category}): {hits_1[fault][0]}/{asked} @1, "
            f"{hit3}/{asked} @3; {sample.relevant_in_index} relevant in index{flag}"
        )
    return lines


def _scenario_lines(report: ScenarioReport) -> list[str]:
    """Render one scenario's block of the benchmark report.

    Args:
        report: The scenario's raw results.

    Returns:
        The block's lines: the counts behind the metrics, the exact-recall metrics overall
        and split by repeated and size-varied families, the per-fault table, and for the
        held-out scenarios what was retrieved instead, per question.
    """
    results = list(report.queries)
    lines = [
        f"== {report.scenario} ==",
        f"{len(results)} questions against {report.history_sources} history sources, "
        f"top {report.count} retrieved per query.",
        "Relevance rule: a source counts only when it records the question's own fault.",
    ]
    lines += _metric_lines(results, report.count, "All questions")
    repeated = [result for result in results if result.repeated_family]
    lines += _metric_lines(
        [result for result in results if not result.repeated_family],
        report.count,
        "Size-varied families",
    )
    lines += _metric_lines(repeated, report.count, "Repeated families")
    if repeated:
        lines.append(
            "  (repeated families have no size in their labels, so each question can "
            "only repeat a history scenario; a hit here is recall of a near-identical "
            "run, not generalisation - a heuristic, not a proven duplicate)"
        )
    lines += _per_fault_lines(results)
    if report.scenario != ALL_HISTORY:
        lines.append("Retrieved instead (rank order), per question - observation, not a score:")
        for result in results:
            offered = ", ".join(result.retrieved_faults) or "(nothing retrieved)"
            lines.append(f"  {result.question}: {offered}")
    return [*lines, ""]


def render(reports: Sequence[ScenarioReport]) -> str:
    """Render the benchmark's reports as text for the terminal.

    Args:
        reports: One report per scenario.

    Returns:
        The text, one block per scenario.
    """
    return "\n".join(line for report in reports for line in _scenario_lines(report))


def main(arguments: Sequence[str] | None = None, embedder: Embedder | None = None) -> int:
    """Run the retrieval benchmark from the command line.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        embedder: How to embed; None uses the local Ollama embedding model.

    Returns:
        0 when the benchmark ran, 1 if the embedder wasn't reachable or a run, label or
        category couldn't be read.
    """
    parser = argparse.ArgumentParser(
        prog="python -m triage.retrieval_metrics",
        description="Score retrieval on its own: Recall@K and MRR against the question runs.",
    )
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS, help="question runs")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="history runs")
    parser.add_argument(
        "--k", type=int, default=DEFAULT_COUNT, help="sources retrieved per query (default 5)"
    )
    parser.add_argument(
        "--scenario", choices=SCENARIOS, action="append", help="scenario(s); default: all three"
    )
    parser.add_argument(
        "--symptom-index",
        action="store_true",
        help="embed each history source's symptoms alone, keeping its cause and fix out of "
        "the index (what the model is shown is unchanged)",
    )
    parsed = parser.parse_args(arguments)
    if embedder is None:
        embedder = OllamaEmbedder()  # pragma: no cover - needs Ollama
    try:
        retriever = InMemoryRetriever.from_history(
            parsed.history, embedder, symptom_index=parsed.symptom_index
        )
        categories = fault_categories(parsed.history)
        reports = [
            evaluate_retrieval(parsed.questions, retriever, scenario, categories, parsed.k)
            for scenario in parsed.scenario or SCENARIOS
        ]
    except (ModelUnavailableError, CaseError, FileNotFoundError, ValueError) as error:
        sys.stdout.write(f"{error}\n")
        return 1
    mode = (
        "symptoms only (evidence alone)"
        if parsed.symptom_index
        else "full source text (evidence + cause + fix)"
    )
    sys.stdout.write(f"Index embeds: {mode}\n\n")
    sys.stdout.write(render(reports))
    return 0


if __name__ == "__main__":
    sys.exit(main())
