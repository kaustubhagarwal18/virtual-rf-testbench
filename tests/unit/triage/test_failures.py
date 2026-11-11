"""Tests for splitting a failed run into its distinct failures and triaging each one."""

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import PASSING_TEST, write_run
from triage.answer import MalformedAnswerError, Verdict
from triage.evidence import SEQUENCE_FILE, SavedRun, failed_tests, first_failure, full_evidence
from triage.failures import (
    DEFAULT_MAX_FAILURES,
    failure_groups,
    focus,
    triage_failures,
)
from triage.prompt import Fix

STEP_LOG = (
    "06:55:12.000 INFO  step=1 action=verify level offset instrument=generator result=ok\n"
    "06:55:12.076 ERROR step=2 action=read bench configuration instrument=station "
    "reason=ConfigurationError: BENCH_LEVEL_OFFSET_DB is not set\n"
    "06:55:12.120 ERROR step=3 action=read bench configuration instrument=station "
    "reason=ConfigurationError: BENCH_LEVEL_OFFSET_DB is not set\n"
)

AGREED_CAP = 5
"""The cap on failures triaged per run that was agreed for CI."""


def failing(test_id: str, message: str, outcome: str = "failed") -> dict[str, object]:
    """Return a failed test record with the given id and message, and no measurements."""
    return {
        "id": f"tests/bench/test_bench.py::{test_id}",
        "outcome": outcome,
        "message": message,
        "instrument_errors": [],
        "measurements": [],
    }


def stopped(test_id: str, step: int) -> dict[str, object]:
    """Return a test that errored because the setup step it needed failed."""
    return failing(
        test_id,
        "tests.utilities.sequencer.StepFailedError: "
        f"Step {step} (read bench configuration) failed on the station",
        outcome="error",
    )


LEVEL_CASES = [
    failing(f"test_level_accuracy[{level}]", f"Failed: level_{level}: -119 dBm, FAIL")
    for level in ("-30.0", "-20.0", "0.0")
]
TIMEOUT = failing("test_level_accuracy[10.0]", "drivers.session.InstrumentTimeoutError: no reply")
HARMONIC = failing("test_second_harmonic", "Failed: second_harmonic: -1.6 dBc, FAIL")


def load(tmp_path: Path, tests: list[dict[str, object]], sequence: str = "") -> SavedRun:
    """Write a run with these tests and load it."""
    return SavedRun.load(write_run(tmp_path / "run", tests, sequence))


def test_a_passing_run_has_no_failures(tmp_path: Path) -> None:
    """Nothing failed, so there is nothing to triage."""
    assert not failure_groups(load(tmp_path, [PASSING_TEST]))


def test_cases_of_one_test_failing_the_same_way_are_one_failure(tmp_path: Path) -> None:
    """Three parametrized cases with the same kind of failure are asked about once."""
    groups = failure_groups(load(tmp_path, [*LEVEL_CASES, PASSING_TEST]))
    assert len(groups) == 1
    assert len(groups[0].test_ids) == len(LEVEL_CASES)


def test_another_test_or_another_kind_of_failure_is_another_failure(tmp_path: Path) -> None:
    """A different test, or the same test failing with a timeout, is triaged on its own."""
    groups = failure_groups(load(tmp_path, [*LEVEL_CASES, TIMEOUT, HARMONIC]))
    assert [len(group.test_ids) for group in groups] == [3, 1, 1]
    assert len({group.description for group in groups}) == len(groups)


def test_tests_stopped_by_one_setup_step_are_one_failure_that_comes_first(
    tmp_path: Path,
) -> None:
    """Every test a failed setup step stopped joins that step, ahead of the tests' own failures."""
    run = load(tmp_path, [HARMONIC, stopped("test_a", 2), stopped("test_b", 3)], STEP_LOG)
    groups = failure_groups(run)
    assert "read bench configuration" in groups[0].description
    assert [group.test_ids for group in groups] == [
        ("test_a", "test_b"),
        ("test_second_harmonic",),
    ]


def test_a_focused_run_holds_only_its_own_failed_tests(tmp_path: Path) -> None:
    """Focusing keeps the passed tests and the group's failures, and drops the others."""
    run = load(tmp_path, [*LEVEL_CASES, HARMONIC, PASSING_TEST])
    harmonic = failure_groups(run)[1]
    focused = focus(run, harmonic, failure_groups(run))
    assert [test["id"] for test in failed_tests(focused)] == [HARMONIC["id"]]
    assert focused.logs == run.logs
    assert focused.results["tests"][-1] == PASSING_TEST


def test_a_focused_test_failure_is_not_led_by_another_failures_setup_step(
    tmp_path: Path,
) -> None:
    """A step that failed for other tests is not this test's first failure."""
    run = load(tmp_path, [stopped("test_a", 2), HARMONIC], STEP_LOG)
    step, harmonic = failure_groups(run)
    focused_test = first_failure(focus(run, harmonic, [step, harmonic]))
    focused_step = first_failure(focus(run, step, [step, harmonic]))
    assert focused_test is not None
    assert focused_test.source != SEQUENCE_FILE
    assert focused_step is not None
    assert focused_step.source == SEQUENCE_FILE


def test_the_focused_evidence_counts_its_tests_and_names_the_other_failures(
    tmp_path: Path,
) -> None:
    """The model learns how many tests failed this way and that other failures exist."""
    run = load(tmp_path, [*LEVEL_CASES, HARMONIC])
    level, harmonic = failure_groups(run)
    evidence = full_evidence(focus(run, level, [level, harmonic]))
    assert "3 tests failed this way." in evidence
    assert harmonic.description in evidence
    assert "further tests failed after it" not in evidence


def test_an_unfocused_run_keeps_its_evidence_unchanged(tmp_path: Path) -> None:
    """History runs and the API's single answer see the same evidence as before."""
    run = load(tmp_path, [*LEVEL_CASES, HARMONIC])
    evidence = full_evidence(run)
    assert "3 further tests failed after it, which may be consequences." in evidence
    assert "failed this way" not in evidence


def answer_by_test(
    answers: dict[str, Fix],
) -> tuple[list[str], Callable[[SavedRun], Verdict]]:
    """Return a record of the failed tests asked about and an answerer keyed by test name."""
    asked: list[str] = []

    def answer(run: SavedRun) -> Verdict:
        """Answer with the fix registered for the focused run's first failed test."""
        name = str(failed_tests(run)[0]["id"]).rsplit("::", maxsplit=1)[-1]
        asked.append(name)
        return Verdict(answers[name], "here", f"because of {name}", [])

    return asked, answer


def test_each_failure_is_asked_once_and_the_rest_are_listed_past_the_cap(
    tmp_path: Path,
) -> None:
    """Only the first few failures are triaged; the others are named, not dropped."""
    tests = [failing(f"test_{number}", "Failed: x") for number in range(4)]
    asked, answer = answer_by_test({f"test_{number}": Fix.CABLE for number in range(4)})
    result = triage_failures(load(tmp_path, tests), answer, limit=3)
    assert asked == ["test_0", "test_1", "test_2"]
    assert [group.test_ids for group in result.untriaged] == [("test_3",)]
    assert result.failed_tests == len(tests)
    assert result.distinct_failures == len(tests)


def test_failures_with_the_same_answer_are_merged(tmp_path: Path) -> None:
    """Two failures that both get CABLE are one answer naming both; ALIGNMENT stays apart."""
    tests = [failing(name, "Failed: x") for name in ("test_a", "test_b", "test_c")]
    _, answer = answer_by_test({"test_a": Fix.CABLE, "test_b": Fix.ALIGNMENT, "test_c": Fix.CABLE})
    result = triage_failures(load(tmp_path, tests), answer)
    assert [answered.verdict.fix for answered in result.answers if answered.verdict] == [
        Fix.CABLE,
        Fix.ALIGNMENT,
    ]
    assert result.answers[0].test_ids == ("test_a", "test_c")


def test_an_unusable_answer_for_one_failure_keeps_the_others(tmp_path: Path) -> None:
    """One malformed reply becomes that failure's problem, not the whole run's."""
    tests = [failing(name, "Failed: x") for name in ("test_a", "test_b")]

    def answer(run: SavedRun) -> Verdict:
        """Fail on test_a and answer CABLE for anything else."""
        if str(failed_tests(run)[0]["id"]).endswith("test_a"):
            raise MalformedAnswerError("not JSON")
        return Verdict(Fix.CABLE, "here", "why", [])

    result = triage_failures(load(tmp_path, tests), answer)
    assert result.answers[0].verdict is None
    assert "not JSON" in result.answers[0].problem
    assert result.answers[1].verdict is not None


def test_the_default_cap_is_five() -> None:
    """Five failures keep a CPU runner's triage to a few minutes, as agreed."""
    assert DEFAULT_MAX_FAILURES == AGREED_CAP


@pytest.mark.parametrize(
    "name",
    sorted(path.name for path in Path("triage/data/runs/questions").iterdir() if path.is_dir()),
)
def test_every_question_run_splits_into_failures_covering_every_failed_test(name: str) -> None:
    """On the recorded runs, each failed test lands in exactly one failure."""
    run = SavedRun.load(Path("triage/data/runs/questions") / name)
    groups = failure_groups(run)
    covered = [test_id for group in groups for test_id in group.test_ids]
    assert sorted(covered) == sorted(
        str(test["id"]).rsplit("::", maxsplit=1)[-1] for test in failed_tests(run)
    )
    assert groups, f"{name} failed but split into no failures"


def test_a_message_naming_no_exception_is_grouped_by_its_outcome(tmp_path: Path) -> None:
    """A message without an exception class falls back to the outcome as its kind."""
    plain = failing("test_plain", "the reading never settled", outcome="error")
    assert failure_groups(load(tmp_path, [plain]))[0].description == "test_plain: error"
