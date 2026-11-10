"""Tests for reading a saved run and finding what failed first."""

import json
from pathlib import Path

import pytest

from triage.evidence import (
    ERROR_ONLY,
    EVALUATED_LEVELS,
    FULL,
    FULL_WITHOUT_FIRMWARE,
    LEVELS,
    NO_SIGNAL_LOG,
    SavedRun,
    build_evidence,
    firmware_changed,
    firmware_note,
    first_failure,
    sequence_fields,
)

PASSING_TEST: dict[str, object] = {
    "id": "tests/bench/test_bench.py::test_passes",
    "outcome": "passed",
    "message": "",
    "instrument_errors": [],
    "measurements": [],
}
FAILING_TEST: dict[str, object] = {
    "id": "tests/bench/test_bench.py::test_level_at_1ghz",
    "outcome": "failed",
    "message": "Failed: level_1000mhz: -13.0 dBm, limit -12 to -11 dBm, margin -1.0 dB, FAIL",
    "instrument_errors": [],
    "measurements": [
        {
            "name": "level_1000mhz",
            "value": -13.0,
            "unit": "dBm",
            "limit_low": -12.0,
            "limit_high": -11.0,
            "margin": -1.0,
            "passed": False,
        }
    ],
}


def write_run(folder: Path, tests: list[dict[str, object]], sequence: str = "") -> Path:
    """Write a saved run with the given tests, and a sequence log when one is given."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "results.json").write_text(json.dumps({"tests": tests}), encoding="utf-8")
    (folder / "logs.txt").write_text("generator-1 | {}\n", encoding="utf-8")
    if sequence:
        (folder / "sequence.log").write_text(sequence, encoding="utf-8")
    (folder / "label.json").write_text('{"category": "bench"}', encoding="utf-8")
    return folder


def test_a_reason_containing_an_equals_sign_is_read_whole() -> None:
    """An error message with '=' in it isn't mistaken for more fields."""
    line = (
        "17:10:59.799 ERROR step=1 action=read bench configuration instrument=station "
        "reason=ConfigurationError: BENCH_LEVEL_OFFSET_DB=80 is outside the range"
    )
    fields = sequence_fields(line)
    assert fields["step"] == "1"
    assert fields["action"] == "read bench configuration"
    assert fields["instrument"] == "station"
    assert fields["reason"] == ("ConfigurationError: BENCH_LEVEL_OFFSET_DB=80 is outside the range")
    assert "BENCH_LEVEL_OFFSET_DB" not in fields


def test_a_line_that_isnt_a_step_has_no_fields() -> None:
    """Anything that doesn't look like a sequence line is ignored rather than guessed at."""
    assert not sequence_fields("")


def test_the_label_is_never_read(tmp_path: Path) -> None:
    """The run carries its evidence only: the answer beside it is not loaded."""
    run = SavedRun.load(write_run(tmp_path / "run", [PASSING_TEST]))
    assert "category" not in json.dumps(run.results)
    assert not hasattr(run, "label")


def test_a_folder_without_results_is_refused(tmp_path: Path) -> None:
    """A folder that isn't a saved run says so, rather than looking like an empty one."""
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError, match="isn't a saved run"):
        SavedRun.load(tmp_path / "empty")


def test_a_run_where_everything_passed_has_no_first_failure(tmp_path: Path) -> None:
    """Nothing to triage means nothing is invented."""
    run = SavedRun.load(write_run(tmp_path / "clean", [PASSING_TEST]))
    assert first_failure(run) is None


def test_a_failed_measurement_is_the_first_failure_with_its_numbers(tmp_path: Path) -> None:
    """Without a failed step, the first failing test is the anchor, with its margins."""
    run = SavedRun.load(write_run(tmp_path / "level", [PASSING_TEST, FAILING_TEST]))
    failure = first_failure(run)
    assert failure is not None
    assert failure.source == "results.json"
    assert failure.where == "test_level_at_1ghz"
    assert failure.consequences == 0
    assert "margin -1.0" in failure.parameters["level_1000mhz"]


def test_a_failed_setup_step_comes_before_any_test(tmp_path: Path) -> None:
    """A failed setup step outranks the test failures that follow it.

    When the sequence that prepares the instruments stops, those tests are the consequence,
    not the cause.
    """
    sequence = (
        "17:00:00.001 INFO  step=1 action=read bench configuration instrument=station result=ok\n"
        "17:00:00.002 ERROR step=2 action=verify level offset instrument=generator "
        "parameter=level_offset_db expected=0.0 reason=AssertionError: it carries -2 dB\n"
    )
    run = SavedRun.load(write_run(tmp_path / "leak", [FAILING_TEST, FAILING_TEST], sequence))
    failure = first_failure(run)
    assert failure is not None
    assert failure.source == "sequence.log"
    assert failure.where == "step 2 (verify level offset) on the generator"
    assert failure.detail == "AssertionError: it carries -2 dB"
    assert failure.parameters == {"parameter": "level_offset_db", "expected": "0.0"}
    assert failure.consequences == 1


def test_the_evidence_doesnt_repeat_the_same_numbers_twice(tmp_path: Path) -> None:
    """A test that failed several measurements lists them once, as parameters."""
    failing = {
        **FAILING_TEST,
        "message": "Failed: a: 1 dB, limit 0 to 0 dB, margin -1 dB, FAIL\nb: 2 dB, FAIL",
    }
    run = SavedRun.load(write_run(tmp_path / "many", [failing]))
    evidence = build_evidence(run, FULL)
    assert evidence.count("level_1000mhz") == 1
    assert "b: 2 dB, FAIL" not in evidence


def test_the_evidence_says_whether_the_firmware_moved(tmp_path: Path) -> None:
    """Whether the version changed is what separates a regression from a bench fault."""
    folder = write_run(tmp_path / "fw", [FAILING_TEST])
    results = json.loads((folder / "results.json").read_text())
    results["firmware_versions"] = {"generator": "0.2.0-rc2", "analyzer": "0.2.0-rc2"}
    (folder / "results.json").write_text(json.dumps(results), encoding="utf-8")
    assert "changed since the last passing run" in build_evidence(SavedRun.load(folder), FULL)


def test_error_only_evidence_is_just_the_message(tmp_path: Path) -> None:
    """The smallest scenario gets what a person would paste, and nothing more."""
    run = SavedRun.load(write_run(tmp_path / "bare", [FAILING_TEST]))
    evidence = build_evidence(run, ERROR_ONLY)
    assert evidence.startswith("Failed: level_1000mhz: -13.0 dBm")
    assert "Firmware" not in evidence


def test_an_unknown_evidence_level_is_refused(tmp_path: Path) -> None:
    """A typo in the mode name fails loudly rather than quietly measuring something else."""
    run = SavedRun.load(write_run(tmp_path / "x", [FAILING_TEST]))
    with pytest.raises(ValueError, match="unknown evidence level"):
        build_evidence(run, "everything")


def test_evidence_for_a_clean_run_says_so(tmp_path: Path) -> None:
    """A run where everything passed has no failure to describe, at either level."""
    run = SavedRun.load(write_run(tmp_path / "clean", [PASSING_TEST]))
    assert build_evidence(run, ERROR_ONLY) == "Every test passed."
    assert build_evidence(run, FULL) == "Every test passed."


def test_consequences_are_counted_in_the_evidence(tmp_path: Path) -> None:
    """When failures follow the first, the evidence says how many may be consequences."""
    run = SavedRun.load(write_run(tmp_path / "many", [FAILING_TEST, FAILING_TEST, FAILING_TEST]))
    assert "2 further tests failed after it" in build_evidence(run, FULL)


@pytest.mark.parametrize(
    ("versions", "changed"),
    [
        ({"analyzer": "0.1.0", "generator": "0.1.0"}, False),
        ({"analyzer": "0.1.0", "generator": "0.2.0-rc1"}, True),
        ({"analyzer": "0.2.0-rc2", "generator": "0.2.0-rc2"}, True),
        ({}, None),
    ],
)
def test_firmware_changed_is_unknown_when_no_version_was_recorded(
    tmp_path: Path, versions: dict[str, str], changed: bool | None
) -> None:
    """A run with no recorded versions is unknown, never reported as unchanged."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    (folder / "results.json").write_text(
        json.dumps({**results, "firmware_versions": versions}), encoding="utf-8"
    )
    run = SavedRun.load(folder)
    assert firmware_changed(run) is changed
    note = firmware_note(run)
    assert ("unknown" in note) is (changed is None)
    assert ("the same as the last passing run" in note) is (changed is False)
    assert ("firmware" in note.casefold()) is (changed is not False)
    for version in versions.values():
        assert version in note


TIMED_OUT_TEST: dict[str, object] = {
    "id": "tests/bench/test_bench.py::test_reset",
    "outcome": "error",
    "message": "InstrumentTimeoutError: no reply to 'OUTP?'",
    "instrument_errors": ['-113,"Undefined header"'],
    "measurements": [],
}
"""A test that timed out on a query after the instrument rejected the command before it."""


@pytest.mark.parametrize(
    ("errors_per_test", "expected"),
    [
        ([[], []], None),
        ([['-113,"Undefined header"'], []], '-113,"Undefined header", reported by 1 of 2'),
        ([["-113,a"], ["-113,a"]], "-113,a, reported by 2 of 2"),
        ([["-113,a", "-222,b"], ["-222,b"]], "-113,a; -222,b, reported by 2 of 2"),
    ],
)
def test_an_instrument_error_leads_the_full_evidence(
    tmp_path: Path, errors_per_test: list[list[str]], expected: str | None
) -> None:
    """Each distinct SCPI error is named once, first, ahead of the timeout it caused."""
    tests = [
        {**TIMED_OUT_TEST, "id": f"t::{index}", "instrument_errors": errors}
        for index, errors in enumerate(errors_per_test)
    ]
    run = SavedRun.load(write_run(tmp_path / "run", tests))
    lines = build_evidence(run, FULL).splitlines()
    if expected is None:
        assert lines[0].startswith("First failure:")
        assert "Instrument error:" not in "\n".join(lines)
    else:
        assert lines[0] == f"Instrument error: {expected} failed tests."
        assert lines[1].startswith("First failure:")


def test_the_error_only_evidence_stays_the_pasted_message(tmp_path: Path) -> None:
    """Error-only is what a person pastes, so it gains no instrument-error line."""
    run = SavedRun.load(write_run(tmp_path / "run", [TIMED_OUT_TEST]))
    assert build_evidence(run, ERROR_ONLY) == TIMED_OUT_TEST["message"]


def test_the_ablation_drops_only_the_firmware_line(tmp_path: Path) -> None:
    """Without the firmware line, every other line of the full evidence is unchanged."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    results["firmware_versions"] = {"analyzer": "0.1.0", "generator": "0.1.0"}
    (folder / "results.json").write_text(json.dumps(results), encoding="utf-8")
    run = SavedRun.load(folder)
    full = build_evidence(run, FULL).splitlines()
    ablated = build_evidence(run, FULL_WITHOUT_FIRMWARE).splitlines()
    assert [line for line in full if not line.startswith("Instruments:")] == ablated
    assert len(full) == len(ablated) + 1


def test_the_ablation_is_evaluated_but_not_offered_to_callers() -> None:
    """A request may ask for error-only or full; only the evaluation builds the ablation."""
    assert FULL_WITHOUT_FIRMWARE not in LEVELS
    assert set(EVALUATED_LEVELS) == {*LEVELS, FULL_WITHOUT_FIRMWARE}


NO_SIGNAL_LINE = (
    'spectrum-analyzer-1  | {"level": "WARNING", "message": '
    '"No signal reaching the analyzer: nothing is connected", "reading": 7}'
)
"""One analyzer log line saying no signal reached it, as the simulator writes it."""


@pytest.mark.parametrize("count", [0, 1, 40])
def test_a_missing_signal_leads_the_full_evidence(tmp_path: Path, count: int) -> None:
    """When the analyzer logged no signal, the evidence says so first, with how often."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    (folder / "logs.txt").write_text(
        "\n".join(["generator-1 | {}", *[NO_SIGNAL_LINE] * count]) + "\n", encoding="utf-8"
    )
    lines = build_evidence(SavedRun.load(folder), FULL).splitlines()
    if count == 0:
        assert lines[0].startswith("First failure:")
        assert not any(line.startswith("No signal:") for line in lines)
    else:
        times = "once" if count == 1 else f"{count} times"
        assert lines[0] == (
            f'No signal: the analyzer logged "No signal reaching the analyzer" {times}.'
        )
        assert lines[1].startswith("First failure:")


def test_the_no_signal_note_matches_what_the_analyzer_logs() -> None:
    """The phrase the evidence looks for is the one the analyzer writes, so they can't drift."""
    source = Path("simulators/spectrum_analyzer/link.py").read_text(encoding="utf-8")
    assert NO_SIGNAL_LOG in source
