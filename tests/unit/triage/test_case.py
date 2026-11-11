"""Tests for cases: the domain-free shape the pipeline reads, and its case.json format."""

import json
from pathlib import Path

import pytest

from triage.bench_run import RESULTS_FILE, load_case
from triage.case import (
    CASE_FILE,
    Case,
    CaseError,
    Item,
    LogLine,
    Reading,
    Step,
    case_from_dict,
    case_to_dict,
    save_case,
)
from triage.evidence import EVALUATED_LEVELS, build_evidence, first_failure

RECORDED = sorted(
    folder
    for kind in ("history", "questions")
    for folder in Path(f"triage/data/runs/{kind}").iterdir()
)
"""Every recorded bench run."""


@pytest.mark.parametrize(
    "folder", RECORDED, ids=lambda folder: f"{folder.parent.name}/{folder.name}"
)
def test_a_bench_run_saved_as_case_json_gives_the_same_evidence(
    tmp_path: Path, folder: Path
) -> None:
    """case.json loses nothing: the evidence from it matches the bench adapter's at every level."""
    adapted = load_case(folder)
    reloaded = load_case(save_case(adapted, tmp_path).parent)
    assert reloaded.items == adapted.items
    assert reloaded.steps == adapted.steps
    assert reloaded.logs == adapted.logs
    for level in EVALUATED_LEVELS:
        assert build_evidence(reloaded, level) == build_evidence(adapted, level)


def test_case_json_wins_over_a_results_file(tmp_path: Path) -> None:
    """A folder with both is read as the generic case, which a system wrote on purpose."""
    save_case(Case(tmp_path, items=(Item("a", "failed", "boom"),)), tmp_path)
    (tmp_path / RESULTS_FILE).write_text('{"tests": []}', encoding="utf-8")
    assert [item.id for item in load_case(tmp_path).items] == ["a"]


def test_another_format_is_refused_by_name(tmp_path: Path) -> None:
    """A case.json from a later format isn't read as this one."""
    (tmp_path / CASE_FILE).write_text(json.dumps({"format": "triage-case/9"}), encoding="utf-8")
    with pytest.raises(CaseError, match="triage-case/9"):
        load_case(tmp_path)


def test_a_minimal_case_needs_only_its_items(tmp_path: Path) -> None:
    """Steps, logs and versions are optional; missing versions are unknown, not unchanged."""
    case = case_from_dict(
        {"format": "triage-case/1", "items": [{"id": "lot-17", "outcome": "failed"}]}, tmp_path
    )
    assert [item.id for item in case.failed_items] == ["lot-17"]
    assert not case.steps
    assert not case.context.versions


def test_a_failed_step_without_a_target_is_described_without_one(tmp_path: Path) -> None:
    """A generic step need not act on anything named; the evidence doesn't invent a target."""
    step = Step("3", "load the rules", "", failed=True, reason="rules file missing")
    failure = first_failure(Case(tmp_path, items=(Item("t", "error"),), steps=(step,)))
    assert failure is not None
    assert failure.where == "step 3 (load the rules)"


def test_only_warnings_and_errors_are_notable() -> None:
    """A log line's level decides whether the evidence shows it."""
    assert LogLine("x", "WARNING").notable
    assert LogLine("x", "ERROR").notable
    assert not LogLine("x").notable


def test_every_outcome_but_passed_is_a_failure() -> None:
    """Failed, errored and skipped items are all failures, as the bench suite counted them."""
    assert [Item("x", outcome).failed for outcome in ("passed", "failed", "error", "skipped")] == [
        False,
        True,
        True,
        True,
    ]


def test_a_reading_round_trips_through_json(tmp_path: Path) -> None:
    """Limits and margins keep their values and types."""
    reading = Reading("rejected", 3, "payments", 0, 0, -3, passed=False)
    case = Case(tmp_path, items=(Item("b", "failed", readings=(reading,)),))
    assert case_from_dict(json.loads(json.dumps(case_to_dict(case))), tmp_path).items == case.items


def test_two_items_with_one_id_are_refused(tmp_path: Path) -> None:
    """Focusing selects items by id, so a repeated id would mix two failures' evidence."""
    with pytest.raises(CaseError, match="batch-7"):
        Case(tmp_path, items=(Item("batch-7", "failed"), Item("batch-7", "failed")))
    raw = {"format": "triage-case/1", "items": [{"id": "a", "outcome": "failed"}] * 2}
    with pytest.raises(CaseError, match="'a'"):
        case_from_dict(raw, tmp_path)


@pytest.mark.parametrize(
    "raw",
    [
        {"items": [{"id": "a", "outcome": "failed"}], "steps": [{"failed": "false"}]},
        {
            "items": [
                {"id": "a", "outcome": "failed", "readings": [{"name": "x", "passed": "false"}]}
            ]
        },
        {"items": [{"id": "a", "outcome": "failed", "readings": [{"name": "x", "passed": 0}]}]},
    ],
)
def test_a_flag_that_isnt_a_json_boolean_is_refused(tmp_path: Path, raw: dict[str, object]) -> None:
    """'false' as text is true in Python; a producer's mistake must not flip a result."""
    with pytest.raises(CaseError, match="true or false"):
        case_from_dict({"format": "triage-case/1", **raw}, tmp_path)
