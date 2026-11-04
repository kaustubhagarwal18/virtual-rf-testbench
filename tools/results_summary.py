"""Turn pytest's JUnit XML and coverage XML into the Markdown summary CI shows on each run.

Run it with: python -m tools.results_summary reports/junit.xml --coverage reports/coverage.xml
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from defusedxml import ElementTree

MAX_LISTED_FAILURES = 20
SLOWEST_SHOWN = 5
_PARTS_IN_A_SUITE_PATH = 3
"""'tests', the suite folder and at least a module, as in 'tests.unit.test_x'."""


class Outcome(Enum):
    """How one test ended."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class TestResult:
    """One test's result.

    Attributes:
        suite: The folder under tests/ it lives in, for example 'unit'.
        test_id: Its pytest id, for example 'tests/unit/test_x.py::test_y'.
        outcome: Whether it passed, failed (or errored) or was skipped.
        duration_s: How long it took.
        message: The first line of its failure or skip message, if any.
    """

    __test__ = False

    suite: str
    test_id: str
    outcome: Outcome
    duration_s: float
    message: str


@dataclass
class SuiteTotals:
    """Counts for one suite.

    Attributes:
        run: Tests run, including skipped ones.
        passed: Tests that passed.
        failed: Tests that failed or errored.
        skipped: Tests that were skipped.
        duration_s: Total time of the suite's tests.
    """

    run: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_s: float = field(default=0.0)

    def add(self, result: TestResult) -> None:
        """Count one result.

        Args:
            result: The result to count.
        """
        self.run += 1
        self.duration_s += result.duration_s
        if result.outcome is Outcome.PASSED:
            self.passed += 1
        elif result.outcome is Outcome.FAILED:
            self.failed += 1
        else:
            self.skipped += 1


def suite_of(classname: str) -> str:
    """Return the suite a test belongs to, from its JUnit class name.

    Args:
        classname: The dotted module path, for example 'tests.unit.simulators.test_parser'.

    Returns:
        The folder directly under tests/, such as 'unit', or 'other'.
    """
    parts = classname.split(".")
    if len(parts) >= _PARTS_IN_A_SUITE_PATH and parts[0] == "tests":
        return parts[1]
    return "other"


def read_junit(path: Path) -> list[TestResult]:
    """Read every test case from a JUnit XML file written by pytest.

    Args:
        path: The JUnit XML file.

    Returns:
        One result per test case, in file order.
    """
    results: list[TestResult] = []
    for case in ElementTree.fromstring(path.read_bytes()).iter("testcase"):
        classname = case.get("classname", "")
        test_id = _test_id(classname, case.get("name", ""), case.get("file"))
        outcome, message = Outcome.PASSED, ""
        for child in case:
            if child.tag in {"failure", "error"}:
                outcome, message = Outcome.FAILED, _first_line(child.get("message", ""))
            elif child.tag == "skipped":
                outcome, message = Outcome.SKIPPED, _first_line(child.get("message", ""))
        duration_s = float(case.get("time", "0") or 0)
        results.append(TestResult(suite_of(classname), test_id, outcome, duration_s, message))
    return results


def _test_id(classname: str, name: str, file: str | None) -> str:
    """Rebuild a test's pytest id from its JUnit entry.

    Args:
        classname: The dotted module path, plus a class name for a test inside a class.
        name: The test's name, with any parameters.
        file: The test file, which pytest writes with junit_family xunit1; None without it.

    Returns:
        For example 'tests/unit/test_x.py::TestFoo::test_y'.
    """
    if file is None:
        return f"{classname.replace('.', '/')}.py::{name}"
    module = file.removesuffix(".py").replace("/", ".")
    test_class = classname.removeprefix(module).removeprefix(".")
    return "::".join(part for part in (file, test_class, name) if part)


def _first_line(text: str) -> str:
    """Return the first line of a message.

    Args:
        text: The message.

    Returns:
        Its first line, without surrounding spaces.
    """
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def totals_by_suite(results: Sequence[TestResult]) -> dict[str, SuiteTotals]:
    """Count results per suite, in the order the suites first appear.

    Args:
        results: The test results.

    Returns:
        The totals for each suite.
    """
    totals: dict[str, SuiteTotals] = {}
    for result in results:
        totals.setdefault(result.suite, SuiteTotals()).add(result)
    return totals


def read_coverage_percent(path: Path) -> float | None:
    """Read total coverage from a coverage XML file, counting lines and branches as pytest-cov does.

    Args:
        path: The coverage XML file.

    Returns:
        The coverage in percent, or None if the file doesn't exist.
    """
    if not path.exists():
        return None
    root = ElementTree.fromstring(path.read_bytes())
    valid = int(root.get("lines-valid", "0")) + int(root.get("branches-valid", "0"))
    covered = int(root.get("lines-covered", "0")) + int(root.get("branches-covered", "0"))
    return 100.0 * covered / valid if valid else 100.0


def render(
    results: Sequence[TestResult], coverage_percent: float | None, coverage_floor_percent: float
) -> str:
    """Write the Markdown summary.

    Args:
        results: The test results.
        coverage_percent: Total coverage, or None if it wasn't measured.
        coverage_floor_percent: The coverage the build requires.

    Returns:
        A headline, a table per suite, coverage, failures if any, and the slowest tests.
    """
    if not results:
        return "## Test results: no tests ran\n"
    totals = totals_by_suite(results)
    overall = SuiteTotals()
    for result in results:
        overall.add(result)
    lines = [f"## Test results: {_headline(overall)}", ""]
    lines += ["| Suite | Run | Passed | Failed | Skipped | Time |", "|---|--:|--:|--:|--:|--:|"]
    lines += [_row(suite, suite_totals) for suite, suite_totals in totals.items()]
    lines += [
        _row("**Total**", overall, bold=True),
        "",
        _coverage_line(coverage_percent, coverage_floor_percent),
    ]
    lines += _failure_lines(results)
    lines += _slowest_lines(results)
    return "\n".join(lines) + "\n"


def _headline(overall: SuiteTotals) -> str:
    """Summarise the run in a few words, failures first.

    Args:
        overall: The totals across every suite.

    Returns:
        For example '2 failed, 300 passed, 1 skipped'.
    """
    parts = [
        f"{count} {label}"
        for count, label in (
            (overall.failed, "failed"),
            (overall.passed, "passed"),
            (overall.skipped, "skipped"),
        )
        if count
    ]
    return ", ".join(parts)


def _row(name: str, totals: SuiteTotals, *, bold: bool = False) -> str:
    """Write one table row.

    Args:
        name: The row's label.
        totals: The counts to show.
        bold: Whether to show the numbers in bold, for the total row.

    Returns:
        The Markdown table row.
    """
    cells = [
        str(totals.run),
        str(totals.passed),
        str(totals.failed),
        str(totals.skipped),
        f"{totals.duration_s:.1f} s",
    ]
    if bold:
        cells = [f"**{cell}**" for cell in cells]
    return f"| {name} | " + " | ".join(cells) + " |"


def _coverage_line(coverage_percent: float | None, coverage_floor_percent: float) -> str:
    """Write the coverage line.

    Args:
        coverage_percent: Total coverage, or None if it wasn't measured.
        coverage_floor_percent: The coverage the build requires.

    Returns:
        The coverage and the floor, flagged when coverage is below it.
    """
    if coverage_percent is None:
        return "Coverage: not measured"
    if coverage_percent < coverage_floor_percent:
        return f"Coverage: {coverage_percent:.1f}%, below the {coverage_floor_percent:g}% floor"
    return f"Coverage: {coverage_percent:.1f}% (floor {coverage_floor_percent:g}%)"


def _failure_lines(results: Sequence[TestResult]) -> list[str]:
    """List the failures with their messages, up to a limit.

    Args:
        results: The test results.

    Returns:
        A Failures section, or nothing if every test passed or was skipped.
    """
    failures = [result for result in results if result.outcome is Outcome.FAILED]
    if not failures:
        return []
    lines = ["", "### Failures", ""]
    lines += [
        f"- `{result.test_id}`: {result.message.replace('`', chr(39))}"
        for result in failures[:MAX_LISTED_FAILURES]
    ]
    if len(failures) > MAX_LISTED_FAILURES:
        lines.append(f"- and {len(failures) - MAX_LISTED_FAILURES} more")
    return lines


def _slowest_lines(results: Sequence[TestResult]) -> list[str]:
    """List the slowest tests.

    Args:
        results: The test results.

    Returns:
        A short section naming the slowest tests and their times.
    """
    slowest = sorted(results, key=lambda result: result.duration_s, reverse=True)[:SLOWEST_SHOWN]
    return ["", "### Slowest tests", ""] + [
        f"- `{result.test_id}` {result.duration_s:.2f} s" for result in slowest
    ]


def main(arguments: Sequence[str] | None = None) -> int:
    """Print the summary for a JUnit XML file, and optionally a coverage XML file.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.

    Returns:
        0, also when the results file is missing: the summary then says so, and the test
        step that failed to write it has already failed the build.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tools.results_summary", description="Summarise pytest results."
    )
    parser.add_argument("junit", type=Path, help="pytest's JUnit XML file")
    parser.add_argument("--coverage", type=Path, help="coverage XML file")
    parser.add_argument("--coverage-floor", type=float, default=80.0, help="required coverage")
    parsed = parser.parse_args(arguments)
    if not parsed.junit.exists():
        sys.stdout.write(f"## Test results: pytest didn't write {parsed.junit}\n")
        return 0
    coverage = read_coverage_percent(parsed.coverage) if parsed.coverage else None
    sys.stdout.write(render(read_junit(parsed.junit), coverage, parsed.coverage_floor))
    return 0


if __name__ == "__main__":
    sys.exit(main())
