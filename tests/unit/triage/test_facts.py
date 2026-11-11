"""Tests for fact kinds: domain-free statements about a run that a pack's checks rely on."""

import json
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from triage.bench_run import load_case
from triage.case import Case
from triage.facts import (
    FACT_KINDS,
    ErrorsReported,
    LogContains,
    NamesOtherVersion,
    ReadingFailedIn,
    ReadingsFailedOnlyOutside,
    VersionsUnchanged,
    make_fact,
)

VERSION_PATTERN = r"\b\d+\.\d+\.\d+\b"


def run_with(tmp_path: Path, *, versions: dict[str, str] | None = None, **test: object) -> Case:
    """Return a run with one failed test, changed as asked, and the given versions if any."""
    folder = write_run(tmp_path / "run", [{**FAILING_TEST, **test}])
    if versions is not None:
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        results["firmware_versions"] = versions
        (folder / "results.json").write_text(json.dumps(results), encoding="utf-8")
    return load_case(folder)


@pytest.mark.parametrize(
    ("versions", "holds"),
    [({"api": "1.0.0", "worker": "1.0.0"}, True), ({"api": "1.0.1"}, False), ({}, False)],
)
def test_versions_unchanged_holds_only_when_every_recorded_version_is_the_baseline(
    tmp_path: Path, versions: dict[str, str], holds: bool
) -> None:
    """No versions recorded means unknown, which is not the same as unchanged."""
    fact = VersionsUnchanged(baseline="1.0.0")
    assert (fact(run_with(tmp_path, versions=versions), "") is not None) is holds


def test_names_other_version_reports_the_first_foreign_one_and_what_ran(tmp_path: Path) -> None:
    """An answer naming 2.0.0 for a run on 1.0.0 is about another run; the values say so."""
    fact = NamesOtherVersion(pattern=VERSION_PATTERN)
    run = run_with(tmp_path, versions={"worker": "1.0.0", "api": "1.0.0"})
    assert fact(run, "1.0.0 then 2.0.0 broke it") == {
        "mentioned": "2.0.0",
        "ran": "api 1.0.0, worker 1.0.0",
    }
    assert fact(run, "1.0.0 broke it") is None
    assert fact(run_with(tmp_path, versions={}), "2.0.0") is None


def test_errors_reported_gives_the_first_error_of_a_failed_test(tmp_path: Path) -> None:
    """Errors a failed test carries are a fact; the first is quoted."""
    fact = ErrorsReported()
    run = run_with(tmp_path, instrument_errors=["E42 rejected", "E7"])
    assert fact(run, "") == {"error": "E42 rejected"}
    assert fact(run_with(tmp_path), "") is None


def test_log_contains_counts_the_lines(tmp_path: Path) -> None:
    """A phrase in the logs holds as a fact, with how often it appeared."""
    run = run_with(tmp_path)
    (run.folder / "logs.txt").write_text("a\ncard declined\nb\ncard declined\n", encoding="utf-8")
    run = load_case(run.folder)
    assert LogContains(text="card declined")(run, "") == {"count": "2", "times": "2 times"}
    assert LogContains(text="timeout")(run, "") is None


def test_reading_failed_in_units_looks_only_at_out_of_limit_readings(tmp_path: Path) -> None:
    """FAILING_TEST's failed reading is in dBm, so it counts for dBm and not for EUR."""
    run = run_with(tmp_path)
    assert ReadingFailedIn(units=["dBm"])(run, "") == {}
    assert ReadingFailedIn(units=["EUR"])(run, "") is None


def test_every_kind_is_registered_by_its_name() -> None:
    """A pack names a kind by the key it is registered under."""
    assert set(FACT_KINDS) == {
        "versions_unchanged",
        "names_other_version",
        "errors_reported",
        "log_contains",
        "reading_failed_in",
        "readings_failed_only_outside",
    }


@pytest.mark.parametrize(
    ("kind", "params", "message"),
    [
        ("no_such_kind", {}, "unknown fact kind"),
        ("log_contains", {}, "parameters"),
        ("log_contains", {"text": "x", "extra": 1}, "parameters"),
        ("log_contains", {"text": ""}, "text"),
        ("versions_unchanged", {"baseline": 3}, "baseline"),
        ("names_other_version", {"pattern": "("}, "pattern"),
        ("reading_failed_in", {"units": "dB"}, "units"),
        ("log_contains", {"text": "x", "provides": ["error"]}, "provides"),
    ],
)
def test_a_bad_fact_is_refused_with_a_reason(
    kind: str, params: dict[str, object], message: str
) -> None:
    """Unknown kinds, wrong or missing parameters and bad values are refused at load time."""
    with pytest.raises(ValueError, match=message):
        make_fact(kind, params)


def test_readings_failed_only_outside_needs_a_failed_reading_none_in_the_units(
    tmp_path: Path,
) -> None:
    """A dBm failure is inside dBm; a run with no failed reading says nothing either way."""
    run = run_with(tmp_path)
    assert ReadingsFailedOnlyOutside(units=["Hz"])(run, "") == {}
    assert ReadingsFailedOnlyOutside(units=["dBm"])(run, "") is None
    assert ReadingsFailedOnlyOutside(units=["Hz"])(run_with(tmp_path, measurements=[]), "") is None
