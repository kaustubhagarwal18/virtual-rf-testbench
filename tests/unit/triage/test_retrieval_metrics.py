"""Tests for the retrieval benchmark: Recall@K and MRR, scored apart from any model."""

import json
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from triage.embed import FakeEmbedder
from triage.retrieval import InMemoryRetriever
from triage.retrieval_metrics import (
    ALL_HISTORY,
    CATEGORY_HELD_OUT,
    OWN_FAULT_HELD_OUT,
    SCENARIOS,
    QueryResult,
    evaluate_retrieval,
    excluded_faults,
    fault_categories,
    first_relevant_rank,
    main,
    mean_reciprocal_rank,
    per_fault_hits,
    recall_at_k,
    render,
)


def result(
    fault: str = "cable_loss",
    retrieved: tuple[str, ...] = (),
    question: str = "cable_loss-1",
    repeated: bool = False,
) -> QueryResult:
    """Build one query's result by hand, for the pure metric tests."""
    return QueryResult(
        question=question,
        fault=fault,
        category="bench",
        repeated_family=repeated,
        relevant_in_index=1,
        retrieved_faults=retrieved,
        retrieved_ids=tuple(f"past-{index}" for index in range(len(retrieved))),
    )


def seeded_run(
    folder: Path, fault: str, category: str, message: str, unit: str | None = None
) -> Path:
    """Write a labelled run whose single failure carries a distinctive message."""
    run = write_run(folder, [{**FAILING_TEST, "message": message}])
    label: dict[str, object] = {
        "fault": fault,
        "category": category,
        "cause": f"{fault} cause",
        "fix": f"{fault} fix",
    }
    if unit is not None:
        label["unit"] = unit
    (run / "label.json").write_text(json.dumps(label), encoding="utf-8")
    return run


MESSAGES = {
    "cable_loss": "path loss cable datasheet exceeded",
    "slow_responses": "timeout no reply from the port",
    "firmware_regression": "level offset grew after the update",
}
CATEGORIES = {"cable_loss": "bench", "slow_responses": "bench", "firmware_regression": "firmware"}


def seeded_bench(tmp_path: Path) -> tuple[Path, Path, InMemoryRetriever]:
    """Write a three-fault history and matching questions; index the history."""
    history = tmp_path / "history"
    questions = tmp_path / "questions"
    for fault, message in MESSAGES.items():
        seeded_run(history / f"{fault}-1", fault, CATEGORIES[fault], message)
        unit = "dB" if fault == "cable_loss" else None
        seeded_run(questions / f"{fault}-q1", fault, CATEGORIES[fault], message + " again", unit)
    seeded_run(
        questions / "cable_loss-q2",
        "cable_loss",
        "bench",
        MESSAGES["cable_loss"] + " once more",
        unit="dB",
    )
    return history, questions, InMemoryRetriever.from_history(history, FakeEmbedder())


@pytest.mark.parametrize(
    ("retrieved", "rank"),
    [(("cable_loss", "other"), 1), (("other", "cable_loss"), 2), (("other", "worse"), None)],
)
def test_the_first_relevant_rank_is_one_indexed_or_none(
    retrieved: tuple[str, ...], rank: int | None
) -> None:
    """The first retrieved source with the question's fault sets the rank; a miss is None."""
    assert first_relevant_rank(result(retrieved=retrieved)) == rank


def test_recall_counts_a_hit_only_within_the_top_k() -> None:
    """A relevant source at rank 2 misses Recall@1 and hits Recall@3."""
    results = [result(retrieved=("other", "cable_loss", "worse"))]
    assert recall_at_k(results, 1) == 0.0
    assert recall_at_k(results, 3) == 1.0


def test_recall_averages_over_all_queries() -> None:
    """One hit at rank 1 and one miss give Recall@1 of a half."""
    results = [result(retrieved=("cable_loss",)), result(retrieved=("other",))]
    one_of_two = 0.5
    assert recall_at_k(results, 1) == one_of_two


def test_mrr_is_the_mean_of_reciprocal_first_ranks() -> None:
    """Ranks 1 and 2 average to 0.75; a query that misses contributes zero."""
    hit_first = result(retrieved=("cable_loss", "other"))
    hit_second = result(retrieved=("other", "cable_loss"))
    miss = result(retrieved=("other", "worse"))
    mean_of_1_and_half = 0.75
    mean_of_1_and_zero = 0.5
    assert mean_reciprocal_rank([hit_first, hit_second]) == mean_of_1_and_half
    assert mean_reciprocal_rank([hit_first, miss]) == mean_of_1_and_zero


def test_the_metrics_refuse_an_empty_result_list() -> None:
    """A recall or MRR over no queries is a question with no answer, not a zero."""
    with pytest.raises(ValueError, match="at least one result"):
        recall_at_k([], 1)
    with pytest.raises(ValueError, match="at least one result"):
        mean_reciprocal_rank([])


def test_the_relevance_rule_is_injected_not_assumed() -> None:
    """A rule that accepts nothing scores zero even when the exact fault was retrieved."""
    results = [result(retrieved=("cable_loss",))]
    assert recall_at_k(results, 1, relevant=lambda question, source: False) == 0.0
    assert mean_reciprocal_rank(results, relevant=lambda question, source: False) == 0.0


def test_per_fault_hits_aggregates_without_merging_queries() -> None:
    """Two questions of one fault stay two queries: one hit of two, not a boolean."""
    results = [
        result(question="cable_loss-q1", retrieved=("cable_loss",)),
        result(question="cable_loss-q2", retrieved=("other",)),
        result(question="slow-q1", fault="slow_responses", retrieved=("slow_responses",)),
    ]
    assert per_fault_hits(results, 1) == {"cable_loss": (1, 2), "slow_responses": (1, 1)}


def test_fault_categories_reads_the_labels_and_rejects_a_conflict(tmp_path: Path) -> None:
    """Each fault maps to its one category; two categories for one fault is a data error."""
    history = tmp_path / "history"
    seeded_run(history / "cable_loss-1", "cable_loss", "bench", "loss high")
    seeded_run(history / "firmware_regression-1", "firmware_regression", "firmware", "offset")
    assert fault_categories(history) == {
        "cable_loss": "bench",
        "firmware_regression": "firmware",
    }
    seeded_run(history / "cable_loss-2", "cable_loss", "test", "loss high again")
    with pytest.raises(ValueError, match="cable_loss"):
        fault_categories(history)


def test_each_scenario_excludes_what_it_names() -> None:
    """Nothing, the question's own fault, or its whole category - its own fault included."""
    categories = {"cable_loss": "bench", "slow_responses": "bench", "firmware": "firmware"}
    assert excluded_faults("cable_loss", "bench", ALL_HISTORY, categories) == frozenset()
    assert excluded_faults("cable_loss", "bench", OWN_FAULT_HELD_OUT, categories) == frozenset(
        {"cable_loss"}
    )
    assert excluded_faults("cable_loss", "bench", CATEGORY_HELD_OUT, categories) == frozenset(
        {"cable_loss", "slow_responses"}
    )


def test_the_category_hold_out_needs_a_category_and_a_known_scenario() -> None:
    """A question without a category blocks the hold-out, and a scenario typo is an error."""
    with pytest.raises(ValueError, match="no category"):
        excluded_faults("cable_loss", "", CATEGORY_HELD_OUT, {})
    with pytest.raises(ValueError, match="unknown scenario"):
        excluded_faults("cable_loss", "bench", "everything", {})


def test_every_question_keeps_its_own_result(tmp_path: Path) -> None:
    """Two questions of one fault stay two results, named by their own folders."""
    history, questions, retriever = seeded_bench(tmp_path)
    report = evaluate_retrieval(questions, retriever, ALL_HISTORY, fault_categories(history))
    cable = [query for query in report.queries if query.fault == "cable_loss"]
    assert [query.question for query in cable] == ["cable_loss-q1", "cable_loss-q2"]
    questions_written = 4
    history_written = 3
    assert len(report.queries) == questions_written
    assert report.history_sources == history_written


def test_with_all_history_the_matching_fault_is_found_and_counted(tmp_path: Path) -> None:
    """A question retrieves its own fault first, and the report counts the relevant sources."""
    history, questions, retriever = seeded_bench(tmp_path)
    report = evaluate_retrieval(questions, retriever, ALL_HISTORY, fault_categories(history))
    assert recall_at_k(report.queries, 1) == 1.0
    assert all(query.relevant_in_index == 1 for query in report.queries)


def test_the_own_fault_hold_out_genuinely_excludes_every_run_of_it(tmp_path: Path) -> None:
    """No retrieved source carries the question's fault, and none counts as relevant."""
    history, questions, retriever = seeded_bench(tmp_path)
    report = evaluate_retrieval(questions, retriever, OWN_FAULT_HELD_OUT, fault_categories(history))
    for query in report.queries:
        assert query.fault not in query.retrieved_faults
        assert query.relevant_in_index == 0
        assert query.retrieved_faults
    assert recall_at_k(report.queries, 3) == 0.0


def test_the_category_hold_out_offers_only_other_categories(tmp_path: Path) -> None:
    """A bench question sees no bench source at all, only the other categories' runs."""
    history, questions, retriever = seeded_bench(tmp_path)
    categories = fault_categories(history)
    report = evaluate_retrieval(questions, retriever, CATEGORY_HELD_OUT, categories)
    for query in report.queries:
        for retrieved in query.retrieved_faults:
            assert categories[retrieved] != query.category


def test_the_repeated_family_flag_follows_the_label_unit(tmp_path: Path) -> None:
    """A labelled size means variation; no unit flags the family as repeating history."""
    history, questions, retriever = seeded_bench(tmp_path)
    report = evaluate_retrieval(questions, retriever, ALL_HISTORY, fault_categories(history))
    flags = {query.question: query.repeated_family for query in report.queries}
    assert flags["cable_loss-q1"] is False
    assert flags["slow_responses-q1"] is True


def test_the_report_shows_fractions_splits_and_the_held_out_breakdown(tmp_path: Path) -> None:
    """The text names the scenario, the counts behind each metric, and what came back."""
    history, questions, retriever = seeded_bench(tmp_path)
    categories = fault_categories(history)
    reports = [
        evaluate_retrieval(questions, retriever, scenario, categories) for scenario in SCENARIOS
    ]
    text = render(reports)
    assert "== all history ==" in text
    assert "4 questions against 3 history sources" in text
    assert "Recall@1 4/4 = 1.00" in text
    assert "Size-varied families (2 queries):" in text
    assert "Repeated families (2 queries):" in text
    assert "not generalisation" in text
    assert "cable_loss (bench): 2/2 @1, 2/2 @3; 1 relevant in index" in text
    assert "Retrieved instead" in text
    assert "slow_responses-q1: " in text


def test_main_runs_offline_with_an_injected_embedder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI runs every scenario with the fake embedder and prints the report."""
    history, questions, _ = seeded_bench(tmp_path)
    exit_code = main(
        ["--history", str(history), "--questions", str(questions), "--k", "2"],
        embedder=FakeEmbedder(),
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "== all history ==" in out
    assert "== own fault held out ==" in out
    assert "== category held out ==" in out
    assert "Recall@5" not in out


def test_main_names_the_index_mode_it_ran(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report's first line says what was embedded, so two runs can't be confused."""
    history, questions, _ = seeded_bench(tmp_path)
    arguments = ["--history", str(history), "--questions", str(questions)]
    assert main(arguments, embedder=FakeEmbedder()) == 0
    assert "Index embeds: full source text" in capsys.readouterr().out
    assert main([*arguments, "--symptom-index"], embedder=FakeEmbedder()) == 0
    assert "Index embeds: symptoms only" in capsys.readouterr().out


def test_main_reports_a_missing_folder_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A history path that doesn't exist is one line and exit 1, not a traceback."""
    exit_code = main(
        ["--history", str(tmp_path / "nowhere"), "--questions", str(tmp_path / "nowhere")],
        embedder=FakeEmbedder(),
    )
    assert exit_code == 1
    assert len(capsys.readouterr().out.strip().splitlines()) == 1
