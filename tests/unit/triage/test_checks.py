"""Tests for the checks that overrule a verdict contradicting the evidence."""

import itertools
import json
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, NO_SIGNAL_LINE, write_run
from triage.answer import Verdict
from triage.checks import (
    DEFAULT_CHECKS,
    AlignmentWithoutLevelReadingCheck,
    AlignmentWithoutSignalCheck,
    FirmwareUnchangedCheck,
    ForeignReleaseCheck,
    TimeoutWithInstrumentErrorCheck,
    apply_checks,
)
from triage.evidence import SavedRun
from triage.prompt import Fix

FIRMWARE_STATES: dict[str, dict[str, str]] = {
    "unchanged": {"analyzer": "0.1.0", "generator": "0.1.0"},
    "changed": {"analyzer": "0.1.0", "generator": "0.2.0-rc1"},
    "not recorded": {},
}
"""The three things a run can say about its firmware."""


def run_with_firmware(tmp_path: Path, versions: dict[str, str]) -> SavedRun:
    """Return a failed run that recorded the given firmware versions."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    results["firmware_versions"] = versions
    (folder / "results.json").write_text(json.dumps(results), encoding="utf-8")
    return SavedRun.load(folder)


@pytest.mark.parametrize(
    ("fix", "state"), list(itertools.product(list(Fix), sorted(FIRMWARE_STATES)))
)
def test_only_firmware_on_unchanged_firmware_is_overruled(
    tmp_path: Path, fix: Fix, state: str
) -> None:
    """Every action against every firmware state: only FIRMWARE on unchanged becomes UNKNOWN."""
    run = run_with_firmware(tmp_path, FIRMWARE_STATES[state])
    verdict = Verdict(fix, "where", "because", ["past-1"])
    reviewed = FirmwareUnchangedCheck().review(run, verdict)
    if fix is Fix.FIRMWARE and state == "unchanged":
        assert reviewed.fix is Fix.UNKNOWN
        assert "unchanged" in reviewed.overruled
        assert reviewed.cited == ["past-1"]
    else:
        assert reviewed == verdict


def test_no_checks_leave_the_verdict_alone(tmp_path: Path) -> None:
    """With no checks, a measurement of the model alone stays one."""
    run = run_with_firmware(tmp_path, FIRMWARE_STATES["unchanged"])
    verdict = Verdict(Fix.FIRMWARE, "the generator", "levels are off")
    assert apply_checks(run, verdict, ()) == verdict
    assert apply_checks(run, verdict, DEFAULT_CHECKS).fix is Fix.UNKNOWN


def run_with_errors(tmp_path: Path, errors: list[str]) -> SavedRun:
    """Return a failed run whose failing test reports the given instrument errors."""
    test = {**FAILING_TEST, "instrument_errors": errors}
    return SavedRun.load(write_run(tmp_path / "run", [test]))


@pytest.mark.parametrize(
    ("fix", "errors"),
    list(itertools.product(list(Fix), [[], ['-113,"Undefined header"'], ["-222,x", "-113,y"]])),
)
def test_only_a_timeout_with_an_instrument_error_is_overruled(
    tmp_path: Path, fix: Fix, errors: list[str]
) -> None:
    """Every action with and without errors: only TIMEOUT beside an SCPI error becomes UNKNOWN."""
    run = run_with_errors(tmp_path, errors)
    verdict = Verdict(fix, "where", "because", ["past-1"])
    reviewed = TimeoutWithInstrumentErrorCheck().review(run, verdict)
    if fix is Fix.TIMEOUT and errors:
        assert reviewed.fix is Fix.UNKNOWN
        assert errors[0] in reviewed.overruled
        assert reviewed.cited == ["past-1"]
    else:
        assert reviewed == verdict


def test_an_error_in_a_passing_test_doesnt_count(tmp_path: Path) -> None:
    """Only the tests that failed are read: an error a passing test handled isn't evidence."""
    passing: dict[str, object] = {
        "id": "t::passed",
        "outcome": "passed",
        "message": "",
        "instrument_errors": ["-113,x"],
    }
    run = SavedRun.load(write_run(tmp_path / "run", [passing, FAILING_TEST]))
    verdict = Verdict(Fix.TIMEOUT, "session", "slow")
    assert TimeoutWithInstrumentErrorCheck().review(run, verdict) == verdict


RELEASE_MENTIONS: dict[str, tuple[str, str]] = {
    "none": ("the generator", "levels are off"),
    "own release in where": ("generator 0.2.0-rc1", "levels are off"),
    "foreign release in where": ("generator 0.2.0-rc2", "levels are off"),
    "foreign release in because": ("the generator", "release 0.2.0-rc2 keeps the offset."),
    "baseline release": ("generator 0.1.0", "levels are off"),
}
"""Where a verdict may name a release, keyed by what it names relative to the 'changed' run."""


@pytest.mark.parametrize(
    ("fix", "state", "mention"),
    list(itertools.product(list(Fix), sorted(FIRMWARE_STATES), sorted(RELEASE_MENTIONS))),
)
def test_only_firmware_naming_a_release_the_run_didnt_run_is_overruled(
    tmp_path: Path, fix: Fix, state: str, mention: str
) -> None:
    """Every action, firmware state and mention: only FIRMWARE naming a foreign release flips."""
    versions = FIRMWARE_STATES[state]
    run = run_with_firmware(tmp_path, versions)
    where, because = RELEASE_MENTIONS[mention]
    verdict = Verdict(fix, where, because, ["past-1"])
    reviewed = ForeignReleaseCheck().review(run, verdict)
    named = [word.rstrip(".") for word in f"{where} {because}".split() if word[0].isdigit()]
    foreign = bool(versions) and any(release not in versions.values() for release in named)
    if fix is Fix.FIRMWARE and foreign:
        assert reviewed.fix is Fix.UNKNOWN
        assert named[0] in reviewed.overruled
        assert reviewed.cited == ["past-1"]
    else:
        assert reviewed == verdict


def test_the_measured_miss_is_caught_by_the_default_checks(tmp_path: Path) -> None:
    """A run on 0.1.0 blamed on a precedent's 0.2.0-rc2 becomes UNKNOWN, naming the release."""
    run = run_with_firmware(tmp_path, FIRMWARE_STATES["unchanged"])
    verdict = Verdict(
        Fix.FIRMWARE,
        "generator 0.2.0-rc2",
        "The regression is due to the release 0.2.0-rc2 not clearing the level offset",
    )
    reviewed = apply_checks(run, verdict, DEFAULT_CHECKS)
    assert reviewed.fix is Fix.UNKNOWN
    assert "0.2.0-rc2" in reviewed.overruled


def test_the_service_applies_every_check() -> None:
    """The default checks are the release, firmware, timeout and two alignment checks, in order."""
    assert [type(check) for check in DEFAULT_CHECKS] == [
        ForeignReleaseCheck,
        FirmwareUnchangedCheck,
        TimeoutWithInstrumentErrorCheck,
        AlignmentWithoutSignalCheck,
        AlignmentWithoutLevelReadingCheck,
    ]


def run_with_no_signal(tmp_path: Path, warnings: int) -> SavedRun:
    """Return a failed run whose analyzer logged no signal the given number of times."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    (folder / "logs.txt").write_text(
        "\n".join(["generator-1 | {}", *[NO_SIGNAL_LINE] * warnings]) + "\n", encoding="utf-8"
    )
    return SavedRun.load(folder)


@pytest.mark.parametrize(("fix", "warnings"), list(itertools.product(list(Fix), [0, 1, 22])))
def test_only_alignment_without_a_signal_is_overruled(
    tmp_path: Path, fix: Fix, warnings: int
) -> None:
    """Every action with and without the warning: only ALIGNMENT with no signal becomes UNKNOWN."""
    run = run_with_no_signal(tmp_path, warnings)
    verdict = Verdict(fix, "where", "because", ["past-1"])
    reviewed = AlignmentWithoutSignalCheck().review(run, verdict)
    if fix is Fix.ALIGNMENT and warnings:
        assert reviewed.fix is Fix.UNKNOWN
        assert "no signal" in reviewed.overruled
        assert reviewed.cited == ["past-1"]
    else:
        assert reviewed == verdict


FAILED_UNITS: dict[str, tuple[str, ...]] = {
    "a level": ("dBm",),
    "a path loss": ("dB",),
    "a level and a frequency": ("Hz", "dBm"),
    "a frequency": ("Hz",),
    "a relative level": ("dBc",),
    "no reading": (),
}
"""What a failed test's out-of-limit readings can be measured in."""

LEVEL_CASES = {"a level", "a path loss", "a level and a frequency"}
"""The cases with a level or power reading out of limits, which alignment can explain."""


def run_with_failed_units(tmp_path: Path, units: tuple[str, ...]) -> SavedRun:
    """Return a failed run whose one failed test has out-of-limit readings in these units."""
    measurement = FAILING_TEST["measurements"][0]  # type: ignore[index]
    measurements = [{**measurement, "name": f"reading_{unit}", "unit": unit} for unit in units]
    test = {**FAILING_TEST, "measurements": measurements}
    return SavedRun.load(write_run(tmp_path / "run", [test]))


@pytest.mark.parametrize(("fix", "case"), list(itertools.product(list(Fix), sorted(FAILED_UNITS))))
def test_only_alignment_without_a_failed_level_reading_is_overruled(
    tmp_path: Path, fix: Fix, case: str
) -> None:
    """Alignment shifts levels, not frequencies: ALIGNMENT without a failed level is refused."""
    run = run_with_failed_units(tmp_path, FAILED_UNITS[case])
    verdict = Verdict(fix, "analyzer", "because", ["past-1"])
    reviewed = AlignmentWithoutLevelReadingCheck().review(run, verdict)
    if fix is Fix.ALIGNMENT and case not in LEVEL_CASES:
        assert reviewed.fix is Fix.UNKNOWN
        assert "no level or power reading" in reviewed.overruled
    else:
        assert reviewed == verdict
