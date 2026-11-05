"""Tests for scoring triage against the question runs, with a fake model."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from tools.faults import CATALOGUE
from triage.evaluate import EXPECTED_FIX, MALFORMED, evaluate, expected_fix, main, render
from triage.evidence import ERROR_ONLY, FULL, SavedRun
from triage.llm import FakeProvider
from triage.prompt import Fix


def question(folder: Path, fault: str) -> Path:
    """Write a question run folder labelled with a fault."""
    run = write_run(folder, [FAILING_TEST])
    (run / "label.json").write_text(json.dumps({"fault": fault}), encoding="utf-8")
    return run


def verdict(fix: Fix) -> str:
    """A well-formed reply choosing a given fix."""
    return json.dumps({"fix": fix.name, "where": "x", "because": "y", "cited": []})


def test_every_catalogue_fault_has_an_expected_fix() -> None:
    """A fault with no scoring key would silently never be counted right."""
    assert set(CATALOGUE) <= set(EXPECTED_FIX)


def test_the_expected_fix_comes_from_the_label(tmp_path: Path) -> None:
    """The label is the answer key; the fix it maps to is what the verdict is scored against."""
    run = SavedRun.load(question(tmp_path / "cable_loss", "cable_loss"))
    assert expected_fix(run) is Fix.CABLE


def test_a_right_answer_is_counted_and_a_wrong_one_is_tallied(tmp_path: Path) -> None:
    """One question answered right, one wrong: the score is one, and the wrong choice is kept."""
    questions = tmp_path / "questions"
    question(questions / "cable_loss", "cable_loss")
    question(questions / "slow_responses", "slow_responses")
    # Right on cable (CABLE), wrong on slow_responses (says CABLE, should be TIMEOUT).
    provider = FakeProvider([verdict(Fix.CABLE)])
    results = evaluate(questions, provider, [FULL])
    result = results[0]
    assert result.right == 1
    assert result.wrong_choices == {"CABLE": 1}


def test_a_malformed_reply_is_not_counted_right(tmp_path: Path) -> None:
    """A reply that can't be parsed is neither right nor a real wrong answer."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    result = evaluate(tmp_path / "q", FakeProvider(["not json at all"]), [FULL])[0]
    assert result.right == 0
    assert result.wrong_choices == {MALFORMED: 1}


def test_both_scenarios_ask_the_same_questions(tmp_path: Path) -> None:
    """Error-only and full evidence are scored over the same runs."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    results = evaluate(tmp_path / "q", FakeProvider([verdict(Fix.CABLE)]), [ERROR_ONLY, FULL])
    assert [r.level for r in results] == [ERROR_ONLY, FULL]
    assert all(len(r.outcomes) == 1 for r in results)


def test_the_report_shows_counts_not_percentages(tmp_path: Path) -> None:
    """Nine-question results are reported as 'n of m', and misses are marked."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    result = evaluate(tmp_path / "q", FakeProvider([verdict(Fix.LINK)]), [FULL])[0]
    report = render([result], "qwen2.5:3b")
    assert "full evidence: 0 of 1" in report
    assert "MISS" in report
    assert "%" not in report


def test_the_cli_runs_the_scored_scenarios(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Make eval scores the question runs and prints the table."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    assert (
        main(
            ["--questions", str(tmp_path / "q"), "--level", "full"],
            FakeProvider([verdict(Fix.CABLE)]),
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "full evidence: 1 of 1" in out


def test_an_unknown_fault_has_no_expected_fix(tmp_path: Path) -> None:
    """A run labelled with a fault not in the scoring key is an error, not a silent miss."""
    run = SavedRun.load(question(tmp_path / "mystery", "brand_new_fault"))
    with pytest.raises(KeyError, match="no expected fix"):
        expected_fix(run)


def test_evaluate_runs_as_a_module() -> None:
    """Python -m triage.evaluate works the way make eval calls it."""
    completed = subprocess.run(
        [sys.executable, "-m", "triage.evaluate", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Score triage" in completed.stdout
