"""Tests for the checks that overrule a verdict contradicting the evidence."""

import itertools
import json
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from triage.answer import Verdict
from triage.checks import DEFAULT_CHECKS, FirmwareUnchangedCheck, apply_checks
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
