"""Tests for the checks that overrule a verdict contradicting the evidence."""

import itertools
import json
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, NO_SIGNAL_LINE, write_run
from tests.unit.triage.test_pack import WITH_CHECKS, write_pack
from triage.answer import Verdict
from triage.checks import DEFAULT_CHECKS, RuleCheck, VerdictCheck, apply_checks, checks_for
from triage.evidence import SavedRun
from triage.pack import DEFAULT_PACK, UNKNOWN, Action, load_pack


def check_named(name: str) -> VerdictCheck:
    """Return the default pack's check with this name."""
    return next(
        check
        for check in DEFAULT_CHECKS
        if isinstance(check, RuleCheck) and check.rule.name == name
    )


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
    ("fix", "state"), list(itertools.product(list(DEFAULT_PACK.actions), sorted(FIRMWARE_STATES)))
)
def test_only_firmware_on_unchanged_firmware_is_overruled(
    tmp_path: Path, fix: Action, state: str
) -> None:
    """Every action against every firmware state: only FIRMWARE on unchanged becomes UNKNOWN."""
    run = run_with_firmware(tmp_path, FIRMWARE_STATES[state])
    verdict = Verdict(fix, "where", "because", ["past-1"])
    reviewed = check_named("firmware-unchanged").review(run, verdict)
    if fix is DEFAULT_PACK.action("FIRMWARE") and state == "unchanged":
        assert reviewed.fix is UNKNOWN
        assert "unchanged" in reviewed.overruled
        assert reviewed.cited == ["past-1"]
    else:
        assert reviewed == verdict


def test_no_checks_leave_the_verdict_alone(tmp_path: Path) -> None:
    """With no checks, a measurement of the model alone stays one."""
    run = run_with_firmware(tmp_path, FIRMWARE_STATES["unchanged"])
    verdict = Verdict(DEFAULT_PACK.action("FIRMWARE"), "the generator", "levels are off")
    assert apply_checks(run, verdict, ()) == verdict
    assert apply_checks(run, verdict, DEFAULT_CHECKS).fix is UNKNOWN


def run_with_errors(tmp_path: Path, errors: list[str]) -> SavedRun:
    """Return a failed run whose failing test reports the given instrument errors."""
    test = {**FAILING_TEST, "instrument_errors": errors}
    return SavedRun.load(write_run(tmp_path / "run", [test]))


@pytest.mark.parametrize(
    ("fix", "errors"),
    list(
        itertools.product(
            list(DEFAULT_PACK.actions), [[], ['-113,"Undefined header"'], ["-222,x", "-113,y"]]
        )
    ),
)
def test_only_a_timeout_with_an_instrument_error_is_overruled(
    tmp_path: Path, fix: Action, errors: list[str]
) -> None:
    """Every action with and without errors: only TIMEOUT beside an SCPI error becomes UNKNOWN."""
    run = run_with_errors(tmp_path, errors)
    verdict = Verdict(fix, "where", "because", ["past-1"])
    reviewed = check_named("timeout-with-instrument-error").review(run, verdict)
    if fix is DEFAULT_PACK.action("TIMEOUT") and errors:
        assert reviewed.fix is UNKNOWN
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
    verdict = Verdict(DEFAULT_PACK.action("TIMEOUT"), "session", "slow")
    assert check_named("timeout-with-instrument-error").review(run, verdict) == verdict


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
    list(
        itertools.product(
            list(DEFAULT_PACK.actions), sorted(FIRMWARE_STATES), sorted(RELEASE_MENTIONS)
        )
    ),
)
def test_only_firmware_naming_a_release_the_run_didnt_run_is_overruled(
    tmp_path: Path, fix: Action, state: str, mention: str
) -> None:
    """Every action, firmware state and mention: only FIRMWARE naming a foreign release flips."""
    versions = FIRMWARE_STATES[state]
    run = run_with_firmware(tmp_path, versions)
    where, because = RELEASE_MENTIONS[mention]
    verdict = Verdict(fix, where, because, ["past-1"])
    reviewed = check_named("foreign-release").review(run, verdict)
    named = [word.rstrip(".") for word in f"{where} {because}".split() if word[0].isdigit()]
    foreign = bool(versions) and any(release not in versions.values() for release in named)
    if fix is DEFAULT_PACK.action("FIRMWARE") and foreign:
        assert reviewed.fix is UNKNOWN
        assert named[0] in reviewed.overruled
        assert reviewed.cited == ["past-1"]
    else:
        assert reviewed == verdict


def test_the_measured_miss_is_caught_by_the_default_checks(tmp_path: Path) -> None:
    """A run on 0.1.0 blamed on a precedent's 0.2.0-rc2 becomes UNKNOWN, naming the release."""
    run = run_with_firmware(tmp_path, FIRMWARE_STATES["unchanged"])
    verdict = Verdict(
        DEFAULT_PACK.action("FIRMWARE"),
        "generator 0.2.0-rc2",
        "The regression is due to the release 0.2.0-rc2 not clearing the level offset",
    )
    reviewed = apply_checks(run, verdict, DEFAULT_CHECKS)
    assert reviewed.fix is UNKNOWN
    assert "0.2.0-rc2" in reviewed.overruled


def test_the_service_applies_every_check() -> None:
    """The default checks are the release, firmware, timeout and two alignment checks, in order."""
    assert [check.rule.name for check in DEFAULT_CHECKS if isinstance(check, RuleCheck)] == [
        "foreign-release",
        "firmware-unchanged",
        "timeout-with-instrument-error",
        "alignment-without-signal",
        "alignment-without-level-reading",
    ]


def run_with_no_signal(tmp_path: Path, warnings: int) -> SavedRun:
    """Return a failed run whose analyzer logged no signal the given number of times."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    (folder / "logs.txt").write_text(
        "\n".join(["generator-1 | {}", *[NO_SIGNAL_LINE] * warnings]) + "\n", encoding="utf-8"
    )
    return SavedRun.load(folder)


@pytest.mark.parametrize(
    ("fix", "warnings"), list(itertools.product(list(DEFAULT_PACK.actions), [0, 1, 22]))
)
def test_only_alignment_without_a_signal_is_overruled(
    tmp_path: Path, fix: Action, warnings: int
) -> None:
    """Every action with and without the warning: only ALIGNMENT with no signal becomes UNKNOWN."""
    run = run_with_no_signal(tmp_path, warnings)
    verdict = Verdict(fix, "where", "because", ["past-1"])
    reviewed = check_named("alignment-without-signal").review(run, verdict)
    if fix is DEFAULT_PACK.action("ALIGNMENT") and warnings:
        assert reviewed.fix is UNKNOWN
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


@pytest.mark.parametrize(
    ("fix", "case"), list(itertools.product(list(DEFAULT_PACK.actions), sorted(FAILED_UNITS)))
)
def test_only_alignment_without_a_failed_level_reading_is_overruled(
    tmp_path: Path, fix: Action, case: str
) -> None:
    """Alignment shifts levels, not frequencies: ALIGNMENT without a failed level is refused."""
    run = run_with_failed_units(tmp_path, FAILED_UNITS[case])
    verdict = Verdict(fix, "analyzer", "because", ["past-1"])
    reviewed = check_named("alignment-without-level-reading").review(run, verdict)
    if fix is DEFAULT_PACK.action("ALIGNMENT") and case not in LEVEL_CASES:
        assert reviewed.fix is UNKNOWN
        assert "no level or power reading" in reviewed.overruled
    else:
        assert reviewed == verdict


def payments_run(tmp_path: Path, logs: str) -> SavedRun:
    """Return a failed payments run whose test reported a feed error, with the given logs."""
    test = {**FAILING_TEST, "feed_errors": ["E17 file truncated"]}
    folder = write_run(tmp_path / "run", [test])
    (folder / "logs.txt").write_text(logs, encoding="utf-8")
    return SavedRun.load(folder)


@pytest.mark.parametrize(
    ("logs", "reason"),
    [
        ("batch 7 rejected\n", "no mapping change was deployed"),
        ("mapping v2 deployed\n", "the feed reported E17 file truncated"),
    ],
)
def test_another_domains_rules_refuse_its_own_actions(
    tmp_path: Path, logs: str, reason: str
) -> None:
    """A payments pack's checks apply in order, and quote its own facts; nothing RF is used."""
    pack = load_pack(write_pack(tmp_path, WITH_CHECKS))
    run = payments_run(tmp_path, logs)
    mapping = Verdict(pack.action("FIX_MAPPING"), "the mapping", "fields moved", [])
    feed = Verdict(pack.action("REQUEST_FEED"), "the feed", "file short", [])
    assert apply_checks(run, mapping, checks_for(pack)).overruled == reason
    assert apply_checks(run, feed, checks_for(pack)) == feed
