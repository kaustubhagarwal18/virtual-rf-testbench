"""Tests for scoring triage against the question runs, with a fake model."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from tools.faults import CATALOGUE
from triage.embed import FakeEmbedder
from triage.evaluate import SCENARIOS, evaluate, expected_fix, main, repeated_faults
from triage.evidence import ERROR_ONLY, FULL, SavedRun
from triage.llm import CachingProvider, FakeProvider, ModelUnavailableError
from triage.prompt import Fix
from triage.report import _report_rows, findings, render, render_report, score_line
from triage.retrieval import InMemoryRetriever
from triage.scores import MALFORMED, Outcome, Precedents, Scenario, ScenarioResult, wilson_interval


def question(folder: Path, fault: str) -> Path:
    """Write a question run labelled with a fault and, for a catalogue fault, its action."""
    run = write_run(folder, [FAILING_TEST])
    label: dict[str, str] = {"fault": fault}
    if fault in CATALOGUE:
        label["action"] = CATALOGUE[fault].label.action
    (run / "label.json").write_text(json.dumps(label), encoding="utf-8")
    return run


def verdict(fix: Fix) -> str:
    """A well-formed reply choosing a given fix."""
    return json.dumps({"fix": fix.name, "where": "x", "because": "y", "cited": []})


@pytest.mark.parametrize("name", sorted(CATALOGUE))
def test_every_catalogue_fault_records_an_action_on_the_list(name: str) -> None:
    """A fault's action is a key the model can choose, and never UNKNOWN."""
    action = CATALOGUE[name].label.action
    assert action in Fix.__members__
    assert action != Fix.UNKNOWN.name


SAVED_LABELS = sorted(Path("triage/data/runs").glob("*/*/label.json"))
"""Every saved run's label in the data set."""


@pytest.mark.parametrize(
    "path", SAVED_LABELS, ids=lambda path: f"{path.parent.parent.name}/{path.parent.name}"
)
def test_every_saved_label_records_its_faults_action(path: Path) -> None:
    """The data set's labels agree with the catalogue, so the answer key has one source."""
    label = json.loads(path.read_text(encoding="utf-8"))
    assert label["action"] == CATALOGUE[label["fault"]].label.action


def test_the_data_set_has_a_label_for_every_run() -> None:
    """The label test above covers all 54 runs, not an empty list."""
    expected_labels = 54
    assert len(SAVED_LABELS) == expected_labels


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
    assert result.malformed == 1
    assert result.outcomes[0].chosen == MALFORMED
    assert not result.wrong_choices


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
    """Scores are reported as 'n of m'; the only percentage is the interval's 95% level."""
    question(tmp_path / "q" / "cable_loss", "cable_loss")
    result = evaluate(
        tmp_path / "q", FakeProvider([verdict(Fix.LINK)]), [Scenario("full evidence", FULL)]
    )[0]
    report = render([result], "qwen2.5:3b")
    assert "full evidence: 0 of 1 faults solved" in report
    assert "0 of 1 questions right" in report
    assert "MISS" in report
    assert set(re.findall(r"\d+%", report)) == {"95%"}


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
    assert "full evidence: 1 of 1 faults solved" in out


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


@pytest.mark.parametrize("action", [None, "", "REPLACE_EVERYTHING", "cable", 3])
def test_a_label_without_a_known_action_has_no_expected_fix(tmp_path: Path, action: object) -> None:
    """A missing, unknown or wrongly spelt action is an error naming the fix, not a miss."""
    run = write_run(tmp_path / "mystery", [FAILING_TEST])
    label = {"fault": "brand_new_fault"} if action is None else {"fault": "x", "action": action}
    (run / "label.json").write_text(json.dumps(label), encoding="utf-8")
    with pytest.raises(KeyError, match="make fault-dataset"):
        expected_fix(SavedRun.load(run))


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
    assert "Confident wrong choices: LINK x1" in report


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
    assert "Most common confident wrong choice: CABLE" in text
    assert "Right in every question of every scenario: cable_loss." in text


def history_with(folder: Path, fault: str) -> Path:
    """Write a resolved history run of a fault, with its cause and fix."""
    run = question(folder, fault)
    (run / "label.json").write_text(
        json.dumps({"fault": fault, "cause": f"{fault} cause", "fix": f"{fault} fix"}),
        encoding="utf-8",
    )
    return run


def cited(fix: Fix, source_ids: list[str]) -> str:
    """A well-formed reply choosing a fix and citing the given sources."""
    return json.dumps({"fix": fix.name, "where": "x", "because": "y", "cited": source_ids})


RETRIEVAL = Scenario("full + retrieval", FULL, precedents=Precedents.ALL, adds_to="full evidence")


def test_findings_flag_a_cited_precedent_the_model_did_not_act_on(tmp_path: Path) -> None:
    """A cited-but-unused precedent is reported, keyed by the cited source's labelled fault."""
    history_with(tmp_path / "history" / "cable_loss-1", "cable_loss")
    question(tmp_path / "q" / "cable_loss-1", "cable_loss")
    retriever = InMemoryRetriever.from_history(tmp_path / "history", FakeEmbedder())
    source_id = retriever.sources[0].source_id
    result = evaluate(
        tmp_path / "q", FakeProvider([cited(Fix.LINK, [source_id])]), [RETRIEVAL], retriever
    )[0]
    assert result.outcomes[0].sources.cited == (source_id,)
    assert result.outcomes[0].sources.cited_faults == ("cable_loss",)
    text = " ".join(findings([result]))
    assert "cited the matching past failure yet still chose wrong on 1 question" in text


def test_a_retrieval_scenario_offers_sources_the_model_may_cite(tmp_path: Path) -> None:
    """In the retrieval scenario the model is given past runs under neutral ids, and cites them."""
    history_with(tmp_path / "history" / "cable_loss-1", "cable_loss")
    question(tmp_path / "q" / "cable_loss-1", "cable_loss")
    retriever = InMemoryRetriever.from_history(tmp_path / "history", FakeEmbedder())
    source_id = retriever.sources[0].source_id
    provider = FakeProvider([cited(Fix.CABLE, [source_id])])
    result = evaluate(tmp_path / "q", provider, [RETRIEVAL], retriever)[0]
    assert result.right == 1
    assert result.precedent_offered == 1
    assert f"citing {source_id}" in result.outcomes[0].answer
    assert "cable_loss" not in provider.prompts[0][1].split("SOURCES")[1].split("Cause:")[0]


def test_the_held_out_scenario_never_offers_the_questions_own_fault(tmp_path: Path) -> None:
    """With its own fault held out, the model sees only other faults' runs, and UNKNOWN counts."""
    history = tmp_path / "history"
    history_with(history / "cable_loss-1", "cable_loss")
    history_with(history / "slow_responses-1", "slow_responses")
    question(tmp_path / "q" / "cable_loss-1", "cable_loss")
    retriever = InMemoryRetriever.from_history(history, FakeEmbedder())
    held_out = Scenario("held out", FULL, precedents=Precedents.OWN_FAULT_HELD_OUT)
    provider = FakeProvider([verdict(Fix.UNKNOWN)])
    result = evaluate(tmp_path / "q", provider, [held_out], retriever)[0]
    assert result.outcomes[0].sources.offered_faults == ("slow_responses",)
    assert "cable_loss cause" not in provider.prompts[0][1]
    assert (result.right, result.abstained, result.confidently_wrong) == (0, 1, 0)
    assert "said UNKNOWN 1 times and named a wrong action 0 times" in " ".join(findings([result]))


def test_the_checked_scenario_overrules_firmware_on_unchanged_firmware(tmp_path: Path) -> None:
    """The same FIRMWARE reply is a confident miss unchecked, and UNKNOWN once checked."""
    folder = question(tmp_path / "q" / "cable_loss-1", "cable_loss")
    results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    results["firmware_versions"] = {"analyzer": "0.1.0", "generator": "0.1.0"}
    (folder / "results.json").write_text(json.dumps(results), encoding="utf-8")
    results = evaluate(
        tmp_path / "q",
        CachingProvider(FakeProvider([verdict(Fix.FIRMWARE)])),
        [Scenario("plain", FULL), Scenario("checked", FULL, checked=True, adds_to="plain")],
    )
    plain, checked = results[0], results[1]
    assert (plain.confidently_wrong, plain.abstained) == (1, 0)
    assert (checked.confidently_wrong, checked.abstained) == (0, 1)
    assert "overruled" in checked.outcomes[0].answer
    assert "confident wrong answers went from 1 to 0" in " ".join(findings([plain, checked]))


def test_an_outcome_is_keyed_by_the_labels_fault_not_the_folder(tmp_path: Path) -> None:
    """Several questions per fault live in numbered folders; the label says which fault."""
    question(tmp_path / "q" / "run-a", "cable_loss")
    question(tmp_path / "q" / "run-b", "cable_loss")
    result = evaluate(
        tmp_path / "q", FakeProvider([verdict(Fix.CABLE)]), [Scenario("full evidence", FULL)]
    )[0]
    assert [(o.question, o.fault) for o in result.outcomes] == [
        ("run-a", "cable_loss"),
        ("run-b", "cable_loss"),
    ]


@pytest.mark.parametrize("total", range(1, 31))
def test_the_interval_brackets_every_possible_count(total: int) -> None:
    """For every count of every size up to 30, the interval contains it and stays in range."""
    for successes in range(total + 1):
        low, high = wilson_interval(successes, total)
        assert 0 <= low <= successes <= high <= total
        mirror_low, mirror_high = wilson_interval(total - successes, total)
        assert low == pytest.approx(total - mirror_high)
        assert high == pytest.approx(total - mirror_low)


def test_the_interval_matches_a_worked_value() -> None:
    """6 of 9 gives the published Wilson interval, 0.354 to 0.879, in questions."""
    low, high = wilson_interval(6, 9)
    assert low / 9 == pytest.approx(0.354, abs=1e-3)
    assert high / 9 == pytest.approx(0.879, abs=1e-3)
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_every_scenario_after_the_first_adds_to_one_that_exists() -> None:
    """Each scenario names the one it adds to, so every step's effect can be reported."""
    names = [scenario.name for scenario in SCENARIOS]
    assert not SCENARIOS[0].adds_to
    for scenario in SCENARIOS[1:]:
        assert scenario.adds_to in names[: names.index(scenario.name)]


def test_the_cli_reports_an_unreachable_model_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A model that can't be reached exits 1 with its hint, not a traceback."""

    class Down:
        """A provider that is never reachable."""

        def complete(self, system: str, user: str, schema: object = None) -> str:
            """Fail as an unreachable model does."""
            raise ModelUnavailableError("start it with 'ollama serve'")

    question(tmp_path / "q" / "cable_loss-1", "cable_loss")
    code = main(["--questions", str(tmp_path / "q"), "--scenario", "full evidence"], Down())
    assert code == 1
    assert "ollama serve" in capsys.readouterr().out


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
    history_with(tmp_path / "history" / "cable_loss-1", "cable_loss")
    question(tmp_path / "q" / "cable_loss-1", "cable_loss")
    report = tmp_path / "out" / "rag.md"
    main(
        [
            "--questions",
            str(tmp_path / "q"),
            "--scenario",
            "full + retrieval",
            "--report",
            str(report),
        ],
        FakeProvider([verdict(Fix.CABLE)]),
        InMemoryRetriever.from_history(tmp_path / "history", FakeEmbedder()),
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
            [Scenario("full + retrieval", FULL, precedents=Precedents.ALL)],
        )


@pytest.mark.parametrize(
    ("chosen", "mark"),
    [("CABLE", "✓"), ("UNKNOWN", "?"), (MALFORMED, "!"), ("FIRMWARE", "✗")],
)
def test_each_kind_of_answer_has_its_own_mark(chosen: str, mark: str) -> None:
    """Right, UNKNOWN, unusable and confidently wrong are told apart in the report."""
    outcome = Outcome("cable_loss-1", "cable_loss", Fix.CABLE, chosen)
    row = _report_rows(ScenarioResult(Scenario("s", FULL), [outcome]))[2]
    assert f"| {mark} |" in row


def test_no_results_have_no_findings() -> None:
    """With nothing scored there is nothing to conclude, rather than an error."""
    assert not findings([])


def two_faults(tmp_path: Path) -> Path:
    """Write three cable_loss and two slow_responses questions, numbered like the data set."""
    for number in (1, 2, 3):
        question(tmp_path / "q" / f"cable_loss-{number}", "cable_loss")
    for number in (1, 2):
        question(tmp_path / "q" / f"slow_responses-{number}", "slow_responses")
    return tmp_path / "q"


def test_a_fault_is_solved_only_when_all_its_questions_are(tmp_path: Path) -> None:
    """Two of three cable questions right is a split fault, not a solved one."""
    replies = [verdict(Fix.CABLE), verdict(Fix.LINK), verdict(Fix.CABLE)]
    replies += [verdict(Fix.TIMEOUT)] * 2
    result = evaluate(two_faults(tmp_path), FakeProvider(replies), [Scenario("s", FULL)])[0]
    assert result.by_fault == {"cable_loss": (2, 3), "slow_responses": (2, 2)}
    assert (result.faults, result.faults_solved, result.faults_split) == (2, 1, 1)
    assert (result.right, result.total) == (4, 5)
    assert score_line(result).startswith("1 of 2 faults solved (95% interval ")


def test_the_report_has_a_row_per_fault_and_a_column_per_scenario(tmp_path: Path) -> None:
    """The by-fault table shows each fault's questions right in each scenario."""
    results = evaluate(
        two_faults(tmp_path),
        FakeProvider([verdict(Fix.CABLE)]),
        [Scenario("error only", ERROR_ONLY), Scenario("full evidence", FULL)],
    )
    report = render_report(results, "qwen2.5:3b")
    assert "| Fault | Expected | error only | full evidence |" in report
    assert "| cable_loss | CABLE | 3/3 | 3/3 |" in report
    assert "| slow_responses | TIMEOUT | 0/2 | 0/2 |" in report


def test_the_findings_count_how_often_a_faults_questions_split(tmp_path: Path) -> None:
    """The finding that justifies scoring by fault is computed, not asserted."""
    replies = [verdict(Fix.CABLE), verdict(Fix.LINK), verdict(Fix.CABLE)]
    replies += [verdict(Fix.TIMEOUT)] * 2
    result = evaluate(two_faults(tmp_path), FakeProvider(replies), [Scenario("s", FULL)])[0]
    assert "split (some right, some wrong) in 1 of 2 fault-and-scenario cells" in " ".join(
        findings([result])
    )


def test_the_cli_says_how_many_prompts_reached_the_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run reports how many prompts it asked; a rerun of the same prompts asks none."""
    question(tmp_path / "q" / "cable_loss-1", "cable_loss")
    provider = CachingProvider(FakeProvider([verdict(Fix.CABLE)]))
    arguments = ["--questions", str(tmp_path / "q"), "--scenario", "full evidence"]
    main(arguments, provider)
    first = capsys.readouterr().out
    main(arguments, provider)
    second = capsys.readouterr().out
    assert "asked the model 1 new prompts" in first
    assert "asked the model 0 new prompts" in second


def history_resolved_by(folder: Path, fault: str, action: str) -> Path:
    """Write a resolved history run of a fault whose label records the action taken."""
    run = history_with(folder, fault)
    label = json.loads((run / "label.json").read_text(encoding="utf-8"))
    (run / "label.json").write_text(json.dumps({**label, "action": action}), encoding="utf-8")
    return run


def test_the_gated_scenario_answers_disagreement_without_the_model(tmp_path: Path) -> None:
    """Precedents resolved two ways: UNKNOWN unasked, and the finding counts what it prevented."""
    history = tmp_path / "history"
    history_resolved_by(history / "link_down-1", "link_down", "LINK")
    history_resolved_by(history / "slow_responses-1", "slow_responses", "TIMEOUT")
    question(tmp_path / "q" / "cable_loss-1", "cable_loss")
    retriever = InMemoryRetriever.from_history(history, FakeEmbedder())
    plain = Scenario("checked", FULL, precedents=Precedents.ALL, checked=True)
    gated = Scenario(
        "gated", FULL, precedents=Precedents.ALL, checked=True, gated=True, adds_to="checked"
    )
    provider = CachingProvider(FakeProvider([verdict(Fix.LINK)]))
    results = evaluate(tmp_path / "q", provider, [plain, gated], retriever)
    assert results[0].confidently_wrong == 1
    outcome = results[1].outcomes[0]
    assert (outcome.chosen, outcome.sources.gated) == ("UNKNOWN", True)
    assert outcome.answer.startswith("UNKNOWN, model not asked:")
    assert provider.asked == 1
    text = " ".join(findings(results))
    assert "the gates answered UNKNOWN without asking the model on 1 of 1 questions" in text
    assert "1 of those were confident wrong answers and 0 were right" in text


def test_every_gated_scenario_retrieves() -> None:
    """A gate judges precedents, so a gated scenario without retrieval would measure nothing."""
    gated = [scenario for scenario in SCENARIOS if scenario.gated]
    assert gated
    assert all(scenario.retrieve for scenario in gated)


def sized_question(folder: Path, fault: str, unit: str) -> Path:
    """Write a question run whose label records a unit, or none for a fault without a size."""
    run = question(folder, fault)
    label = json.loads((run / "label.json").read_text(encoding="utf-8"))
    (run / "label.json").write_text(json.dumps({**label, "unit": unit}), encoding="utf-8")
    return run


@pytest.mark.parametrize(
    ("units", "expected"),
    [
        ({"cable_loss": "dB", "link_down": ""}, ["link_down"]),
        ({"cable_loss": "dB", "slow_responses": "s"}, []),
        (
            {"link_down": "", "config_parameter_missing": ""},
            ["config_parameter_missing", "link_down"],
        ),
    ],
)
def test_faults_without_a_unit_are_the_repeated_ones(
    tmp_path: Path, units: dict[str, str], expected: list[str]
) -> None:
    """A fault with no size to vary repeats its history scenario; a sized one doesn't."""
    for fault, unit in units.items():
        sized_question(tmp_path / "q" / f"{fault}-1", fault, unit)
    assert repeated_faults(tmp_path / "q") == expected


def test_the_report_says_how_many_solved_faults_repeat_a_history_run(tmp_path: Path) -> None:
    """The caveat names the repeated faults and counts them among the best retrieval's solved."""
    sized_question(tmp_path / "q" / "cable_loss-1", "cable_loss", "dB")
    sized_question(tmp_path / "q" / "link_down-1", "link_down", "")
    history_with(tmp_path / "history" / "link_down-1", "link_down")
    retriever = InMemoryRetriever.from_history(tmp_path / "history", FakeEmbedder())
    results = evaluate(
        tmp_path / "q",
        FakeProvider([verdict(Fix.LINK)]),
        [Scenario("full evidence", FULL), RETRIEVAL],
        retriever,
    )
    plain, retrieved = results[0], results[1]
    repeated = repeated_faults(tmp_path / "q")
    report = render_report([plain, retrieved], "qwen2.5:3b", repeated=repeated)
    assert "1 faults have no size to vary (link_down)" in report
    assert "Of the 1 faults full + retrieval solved, 1 are among them" in report
    assert "no size to vary" not in render_report([plain, retrieved], "qwen2.5:3b")
    assert "no size to vary" not in render_report([plain], "qwen2.5:3b", repeated=repeated)
