"""Tests for scoring triage against the question runs, with a fake model."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from tools.faults import CATALOGUE
from triage.embed import FakeEmbedder
from triage.evaluate import (
    EXPECTED_FIX,
    MALFORMED,
    Scenario,
    _clip,
    evaluate,
    expected_fix,
    findings,
    main,
    render,
    render_report,
)
from triage.evidence import ERROR_ONLY, FULL, SavedRun
from triage.llm import FakeProvider
from triage.prompt import Fix
from triage.retrieval import InMemoryRetriever


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
    results = evaluate(questions, provider, [Scenario("full evidence", FULL)])
    result = results[0]
    assert result.right == 1
    assert result.wrong_choices == {"CABLE": 1}


def test_a_malformed_reply_is_not_counted_right(tmp_path: Path) -> None:
    """A reply that can't be parsed is neither right nor a real wrong answer."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    result = evaluate(
        tmp_path / "q", FakeProvider(["not json at all"]), [Scenario("full evidence", FULL)]
    )[0]
    assert result.right == 0
    assert result.wrong_choices == {MALFORMED: 1}


def test_both_scenarios_ask_the_same_questions(tmp_path: Path) -> None:
    """Error-only and full evidence are scored over the same runs."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    results = evaluate(
        tmp_path / "q",
        FakeProvider([verdict(Fix.CABLE)]),
        [Scenario("error only", ERROR_ONLY), Scenario("full evidence", FULL)],
    )
    assert [r.name for r in results] == ["error only", "full evidence"]
    assert all(len(r.outcomes) == 1 for r in results)


def test_the_report_shows_counts_not_percentages(tmp_path: Path) -> None:
    """Nine-question results are reported as 'n of m', and misses are marked."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    result = evaluate(
        tmp_path / "q", FakeProvider([verdict(Fix.LINK)]), [Scenario("full evidence", FULL)]
    )[0]
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
            ["--questions", str(tmp_path / "q"), "--scenario", "full evidence"],
            FakeProvider([verdict(Fix.CABLE)]),
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "full evidence: 1 of 1" in out


def test_an_injected_provider_overrides_the_model_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--model picks the Ollama model, but an injected provider (tests, service) still wins."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    assert (
        main(
            ["--questions", str(tmp_path / "q"), "--scenario", "full evidence", "--model", "huge"],
            FakeProvider([verdict(Fix.CABLE)]),
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "huge" not in out  # the injected provider answered, not a model called "huge"


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


def test_the_report_shows_the_error_and_the_answer(tmp_path: Path) -> None:
    """The detailed report pairs each failure the model saw with what it answered, and a diagram."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    result = evaluate(
        tmp_path / "q", FakeProvider([verdict(Fix.LINK)]), [Scenario("full evidence", FULL)]
    )[0]
    report = render_report([result], "qwen2.5:3b")
    assert "mermaid" in report
    assert "never shown" in report
    assert "LINK" in report
    assert "Wrong choices" in report


def test_the_cli_writes_a_report_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Make eval --report writes the detailed report where it is told."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    report = tmp_path / "out" / "baseline.md"
    main(
        [
            "--questions",
            str(tmp_path / "q"),
            "--scenario",
            "full evidence",
            "--report",
            str(report),
        ],
        FakeProvider([verdict(Fix.CABLE)]),
    )
    assert report.exists()
    assert "Triage baseline" in report.read_text(encoding="utf-8")
    assert f"wrote {report}" in capsys.readouterr().out


def test_findings_are_computed_from_the_outcomes(tmp_path: Path) -> None:
    """The report's findings are derived, so they can't drift from the numbers above them."""
    questions = tmp_path / "q"
    question(questions / "cable_loss", "cable_loss")
    question(questions / "slow_responses", "slow_responses")
    # error-only: both wrong (CABLE on both); full: cable right, slow wrong.
    provider = FakeProvider([verdict(Fix.CABLE)])
    results = evaluate(
        questions, provider, [Scenario("error only", ERROR_ONLY), Scenario("full evidence", FULL)]
    )
    text = " ".join(findings(results))
    assert "Best scenario" in text
    assert "Most common wrong choice: CABLE" in text
    assert "cable_loss" in text  # solved under full evidence


def test_findings_flag_a_cited_precedent_the_model_did_not_act_on(tmp_path: Path) -> None:
    """A cited-but-unused precedent makes the findings blame the model, not retrieval."""
    history = tmp_path / "history"
    hist = question(history / "cable_loss-1", "cable_loss")
    (hist / "label.json").write_text(
        json.dumps({"fault": "cable_loss", "cause": "worn cable", "fix": "replaced it"}),
        encoding="utf-8",
    )
    questions = tmp_path / "q"
    question(questions / "cable_loss", "cable_loss")
    retriever = InMemoryRetriever.from_history(history, FakeEmbedder())
    # Cites the matching cable_loss precedent but answers LINK, which is wrong.
    wrong_but_cited = json.dumps(
        {"fix": "LINK", "where": "x", "because": "y", "cited": ["cable_loss-1"]}
    )
    result = evaluate(
        questions,
        FakeProvider([wrong_but_cited]),
        [Scenario("full + retrieval", FULL, retrieve=True)],
        retriever,
    )[0]
    assert result.outcomes[0].cited == ("cable_loss-1",)
    text = " ".join(findings([result]))
    assert "Cited the matching past failure yet still chose wrong: cable_loss" in text
    assert "the ceiling here is the model, not retrieval" in text


def test_clip_shortens_at_a_word_boundary() -> None:
    """Long text is cut to whole words with an ellipsis; short text is returned unchanged."""
    assert _clip("one two three four", 100) == "one two three four"
    assert _clip("one two three four", 9) == "one two..."


def test_a_retrieval_scenario_offers_sources_the_model_may_cite(tmp_path: Path) -> None:
    """In the retrieval scenario the model is given past runs and may cite them."""
    history = tmp_path / "history"
    hist = question(history / "cable_loss-1", "cable_loss")
    (hist / "label.json").write_text(
        json.dumps({"fault": "cable_loss", "cause": "worn cable", "fix": "replaced it"}),
        encoding="utf-8",
    )
    questions = tmp_path / "q"
    question(questions / "cable_loss", "cable_loss")
    retriever = InMemoryRetriever.from_history(history, FakeEmbedder())
    reply = json.dumps(
        {
            "fix": "CABLE",
            "where": "cable",
            "because": "like cable_loss-1",
            "cited": ["cable_loss-1"],
        }
    )
    result = evaluate(
        questions,
        FakeProvider([reply]),
        [Scenario("full + retrieval", FULL, retrieve=True)],
        retriever,
    )[0]
    assert result.right == 1
    assert "citing cable_loss-1" in result.outcomes[0].answer


def test_the_report_heading_uses_the_given_title(tmp_path: Path) -> None:
    """The report's H1 is the title it is given, so a three-scenario run is not mislabelled."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    result = evaluate(
        tmp_path / "q", FakeProvider([verdict(Fix.CABLE)]), [Scenario("full evidence", FULL)]
    )[0]
    assert render_report([result], "qwen2.5:3b", title="Custom heading").startswith(
        "# Custom heading"
    )


def test_a_retrieval_report_is_not_titled_as_the_baseline(tmp_path: Path) -> None:
    """When retrieval is scored, the written report's heading names retrieval, not 'baseline'."""
    history = tmp_path / "history"
    hist = question(history / "cable_loss-1", "cable_loss")
    (hist / "label.json").write_text(
        json.dumps({"fault": "cable_loss", "cause": "worn cable", "fix": "replaced it"}),
        encoding="utf-8",
    )
    questions = tmp_path / "q"
    question(questions / "cable_loss", "cable_loss")
    report = tmp_path / "out" / "rag.md"
    reply = json.dumps(
        {
            "fix": "CABLE",
            "where": "cable",
            "because": "like cable_loss-1",
            "cited": ["cable_loss-1"],
        }
    )
    main(
        ["--questions", str(questions), "--scenario", "full + retrieval", "--report", str(report)],
        FakeProvider([reply]),
        InMemoryRetriever.from_history(history, FakeEmbedder()),
    )
    heading = report.read_text(encoding="utf-8").splitlines()[0]
    assert "retrieval" in heading.lower()
    assert "baseline (no retrieval)" not in heading


def test_a_retrieval_scenario_without_a_retriever_is_an_error(tmp_path: Path) -> None:
    """Asking for retrieval with no index is a programming error, caught loudly."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    with pytest.raises(ValueError, match="no retriever was given"):
        evaluate(
            tmp_path / "q",
            FakeProvider([verdict(Fix.CABLE)]),
            [Scenario("full + retrieval", FULL, retrieve=True)],
        )
