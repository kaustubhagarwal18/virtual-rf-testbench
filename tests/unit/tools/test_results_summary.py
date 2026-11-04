"""Tests for the test results summary that CI shows on each run."""

import subprocess
import sys
from pathlib import Path

import pytest

from tools.results_summary import (
    MAX_LISTED_FAILURES,
    Outcome,
    TestResult,
    main,
    read_coverage_percent,
    read_junit,
    render,
    suite_of,
    summarise_files,
    totals_by_suite,
)

JUNIT_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5">
  <testcase classname="tests.unit.simulators.test_parser" name="test_ok" time="0.010"/>
  <testcase classname="tests.unit.simulators.test_parser" name="test_broken[x]" time="0.020">
    <failure message="AssertionError: assert 1 == 2&#10;more detail">long traceback</failure>
  </testcase>
  <testcase classname="tests.integration.test_bench" name="test_level" time="0.300"/>
  <testcase classname="tests.integration.test_bench" name="test_crash" time="0.050">
    <error message="fixture failed">traceback</error>
  </testcase>
  <testcase classname="tests.unit.simulators.test_physics" name="test_later" time="0.000">
    <skipped message="not ready"/>
  </testcase>
</testsuite></testsuites>
"""

COVERAGE_XML = """<?xml version="1.0" ?>
<coverage lines-valid="10" lines-covered="9" branches-valid="10" branches-covered="8"
          line-rate="0.9" branch-rate="0.8"/>
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    """Write a file under the test's temporary folder and return its path."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def passed(test_id: str, suite: str = "unit", duration_s: float = 0.01) -> TestResult:
    """Build a passing result."""
    return TestResult(suite, test_id, Outcome.PASSED, duration_s, "")


@pytest.mark.parametrize(
    ("classname", "suite"),
    [
        ("tests.unit.simulators.test_parser", "unit"),
        ("tests.integration.test_bench", "integration"),
        ("tests.bench.test_level", "bench"),
        ("tests.test_top_level", "other"),
        ("somewhere.else", "other"),
    ],
)
def test_suite_is_the_folder_under_tests(classname: str, suite: str) -> None:
    """A test's suite is the folder it lives in under tests/."""
    assert suite_of(classname) == suite


def test_junit_results_are_read_with_their_outcomes(tmp_path: Path) -> None:
    """Each test case becomes a result with its suite, id, outcome, time and message."""
    results = read_junit(write(tmp_path, "junit.xml", JUNIT_XML))
    assert [(result.suite, result.outcome) for result in results] == [
        ("unit", Outcome.PASSED),
        ("unit", Outcome.FAILED),
        ("integration", Outcome.PASSED),
        ("integration", Outcome.FAILED),
        ("unit", Outcome.SKIPPED),
    ]
    broken = results[1]
    assert broken.test_id == "tests/unit/simulators/test_parser.py::test_broken[x]"
    assert broken.message == "AssertionError: assert 1 == 2"
    assert broken.duration_s == pytest.approx(0.02)


def test_totals_are_counted_per_suite(tmp_path: Path) -> None:
    """Totals count run, passed, failed and skipped tests, and their time, per suite."""
    totals = totals_by_suite(read_junit(write(tmp_path, "junit.xml", JUNIT_XML)))
    assert list(totals) == ["unit", "integration"]
    unit = totals["unit"]
    assert (unit.run, unit.passed, unit.failed, unit.skipped) == (3, 1, 1, 1)
    assert unit.duration_s == pytest.approx(0.03)


def test_coverage_combines_lines_and_branches(tmp_path: Path) -> None:
    """Coverage is covered lines plus branches over all lines plus branches, as pytest-cov says."""
    assert read_coverage_percent(write(tmp_path, "coverage.xml", COVERAGE_XML)) == pytest.approx(
        85.0
    )


def test_missing_coverage_file_is_none(tmp_path: Path) -> None:
    """Without a coverage file, coverage is unknown rather than zero."""
    assert read_coverage_percent(tmp_path / "missing.xml") is None


def test_summary_of_a_clean_run() -> None:
    """A clean run gets a passed headline, the suite table, coverage and the slowest tests."""
    results = [passed("a.py::fast"), passed("b.py::slow", "integration", duration_s=1.5)]
    summary = render(results, coverage_percent=100.0, coverage_floor_percent=80.0)
    assert summary.startswith("## Test results: 2 passed\n")
    assert "| unit | 1 | 1 | 0 | 0 | 0.0 s |" in summary
    assert "| integration | 1 | 1 | 0 | 0 | 1.5 s |" in summary
    assert "| **Total** | **2** | **2** | **0** | **0** | **1.5 s** |" in summary
    assert "Coverage: 100.0% (floor 80%)" in summary
    assert "`b.py::slow` 1.50 s" in summary
    assert "Failures" not in summary


def test_summary_lists_failures_with_their_messages(tmp_path: Path) -> None:
    """A failing run says how many failed and lists each failure with its message."""
    summary = render(read_junit(write(tmp_path, "junit.xml", JUNIT_XML)), None, 80.0)
    assert summary.startswith("## Test results: 2 failed, 2 passed, 1 skipped\n")
    assert (
        "- `tests/unit/simulators/test_parser.py::test_broken[x]`: AssertionError: assert 1 == 2"
        in summary
    )
    assert "- `tests/integration/test_bench.py::test_crash`: fixture failed" in summary
    assert "Coverage: not measured" in summary


def test_coverage_below_the_floor_is_called_out() -> None:
    """Coverage under the floor is flagged in the summary."""
    summary = render([passed("a.py::t")], coverage_percent=72.5, coverage_floor_percent=80.0)
    assert "Coverage: 72.5%, below the 80% floor" in summary


def test_long_failure_lists_are_cut_short() -> None:
    """Only the first 20 failures are listed; the rest are counted."""
    results = [TestResult("unit", f"t.py::f{n}", Outcome.FAILED, 0.0, "boom") for n in range(25)]
    summary = render(results, None, 80.0)
    assert summary.count("`: boom") == MAX_LISTED_FAILURES
    assert "and 5 more" in summary


def test_backticks_in_messages_cannot_break_the_markdown() -> None:
    """A message containing backticks is shown with plain quotes instead."""
    result = TestResult("unit", "t.py::f", Outcome.FAILED, 0.0, "expected `x`")
    assert "expected 'x'" in render([result], None, 80.0)


def test_no_results_says_so() -> None:
    """An empty results file says that no tests ran."""
    assert render([], None, 80.0).startswith("## Test results: no tests ran\n")


def test_main_prints_the_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The command line reads both files and prints the Markdown summary."""
    junit = write(tmp_path, "junit.xml", JUNIT_XML)
    coverage = write(tmp_path, "coverage.xml", COVERAGE_XML)
    assert main([str(junit), "--coverage", str(coverage)]) == 0
    output = capsys.readouterr().out
    assert output.startswith("## Test results: 2 failed")
    assert "Coverage: 85.0%" in output


def test_main_explains_a_missing_results_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a results file, the summary says pytest wrote none, and the step still succeeds."""
    assert main([str(tmp_path / "junit.xml")]) == 0
    assert "pytest didn't write" in capsys.readouterr().out


def test_captured_output_doesnt_change_the_outcome(tmp_path: Path) -> None:
    """A passing test with captured output (a system-out element) still counts as passed."""
    junit = write(
        tmp_path,
        "junit.xml",
        '<testsuites><testsuite><testcase classname="tests.unit.test_x" name="test_y" time="0.1">'
        "<system-out>log line</system-out></testcase></testsuite></testsuites>",
    )
    assert [result.outcome for result in read_junit(junit)] == [Outcome.PASSED]


def test_runs_as_a_module(tmp_path: Path) -> None:
    """Python -m tools.results_summary works the way make summary calls it."""
    completed = subprocess.run(
        [sys.executable, "-m", "tools.results_summary", str(tmp_path / "missing.xml")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "pytest didn't write" in completed.stdout


def test_ids_use_the_file_and_keep_test_classes(tmp_path: Path) -> None:
    """With the file attribute pytest writes, a test inside a class gets its real pytest id."""
    junit = write(
        tmp_path,
        "junit.xml",
        "<testsuites><testsuite>"
        '<testcase classname="tests.unit.test_x.TestFoo" name="test_y" '
        'file="tests/unit/test_x.py" time="0.1"/>'
        '<testcase classname="tests.unit.test_x" name="test_z" '
        'file="tests/unit/test_x.py" time="0.1"/>'
        "</testsuite></testsuites>",
    )
    assert [result.test_id for result in read_junit(junit)] == [
        "tests/unit/test_x.py::TestFoo::test_y",
        "tests/unit/test_x.py::test_z",
    ]


BENCH_JUNIT_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="1">
  <testcase classname="tests.bench.test_bench" name="test_level" time="0.050"/>
</testsuite></testsuites>
"""


def test_several_results_files_are_combined(tmp_path: Path) -> None:
    """Results from several JUnit files are counted together, each suite in its own row."""
    summary = summarise_files(
        [write(tmp_path, "junit.xml", JUNIT_XML), write(tmp_path, "bench.xml", BENCH_JUNIT_XML)],
        None,
        80.0,
    )
    assert summary.startswith("## Test results: 2 failed, 3 passed, 1 skipped\n")
    assert "| bench | 1 | 1 | 0 | 0 |" in summary
    assert "No results from" not in summary


def test_a_missing_results_file_is_named_next_to_the_others(tmp_path: Path) -> None:
    """When one of several files is missing, the summary names it and still counts the rest."""
    missing = tmp_path / "bench-junit.xml"
    summary = summarise_files([write(tmp_path, "junit.xml", JUNIT_XML), missing], None, 80.0)
    assert summary.startswith("## Test results: 2 failed, 2 passed, 1 skipped\n")
    assert f"No results from `{missing}`: pytest didn't write them." in summary


def test_every_file_missing_names_them_all(tmp_path: Path) -> None:
    """With no results at all, the headline names every file pytest didn't write."""
    first, second = tmp_path / "junit.xml", tmp_path / "bench-junit.xml"
    assert summarise_files([first, second], None, 80.0) == (
        f"## Test results: pytest didn't write `{first}`, `{second}`\n"
    )


def test_main_accepts_several_results_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command line takes any number of JUnit files."""
    junit = write(tmp_path, "junit.xml", JUNIT_XML)
    bench = write(tmp_path, "bench.xml", BENCH_JUNIT_XML)
    assert main([str(junit), str(bench)]) == 0
    assert "| bench |" in capsys.readouterr().out
