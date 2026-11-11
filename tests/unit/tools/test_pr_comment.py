"""Tests for the pull request comment CI posts with the test results."""

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from tools.pr_comment import MARKER, JobResult, main, parse_job, render_comment

PASSING_JUNIT_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2">
  <testcase classname="tests.unit.test_x" name="test_a" time="0.010"/>
  <testcase classname="tests.bench.test_bench" name="test_level" time="0.050"/>
</testsuite></testsuites>
"""

RUN_URL = "https://github.com/owner/repo/actions/runs/1"
COMMIT = "4c0377cdeadbeef"


def test_job_arguments_are_read_as_name_and_result() -> None:
    """NAME=RESULT becomes a job result, with surrounding spaces removed."""
    assert parse_job(" Bench (containers) = failure ") == JobResult("Bench (containers)", "failure")


@pytest.mark.parametrize("text", ["Lint", "=success", "Lint=", "Lint= "])
def test_malformed_job_arguments_are_refused(text: str) -> None:
    """A job argument without a name or a result is an argument error."""
    with pytest.raises(argparse.ArgumentTypeError, match="NAME=RESULT"):
        parse_job(text)


def test_comment_starts_with_the_marker_and_holds_summary_jobs_and_link() -> None:
    """The marker comes first, then the summary, a job table with symbols, and the run link."""
    comment = render_comment(
        "## Test results: 2 passed\n",
        [JobResult("Lint", "success"), JobResult("Bench", "failure"), JobResult("X", "neutral")],
        RUN_URL,
        COMMIT,
    )
    lines = comment.splitlines()
    assert lines[0] == MARKER
    assert lines[1] == "## Test results: 2 passed"
    assert "| Lint | ✅ passed |" in lines
    assert "| Bench | ❌ failed |" in lines
    assert "| X | neutral |" in lines
    assert lines[-1] == f"Commit `4c0377c` · [CI run]({RUN_URL})"


@pytest.mark.parametrize(
    ("result", "label"),
    [
        ("success", "✅ passed"),
        ("failure", "❌ failed"),
        ("cancelled", "⚪ cancelled"),
        ("skipped", "⏭️ skipped"),
    ],
)
def test_every_github_job_result_has_a_label(result: str, label: str) -> None:
    """Each result GitHub Actions reports for a job is shown with its symbol."""
    comment = render_comment(
        "## Test results: 1 passed\n", [JobResult("Job", result)], RUN_URL, COMMIT
    )
    assert f"| Job | {label} |" in comment


def test_main_prints_the_comment_for_results_and_jobs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command line combines the results files, names a missing one, and lists the jobs."""
    junit = tmp_path / "junit.xml"
    junit.write_text(PASSING_JUNIT_XML, encoding="utf-8")
    missing = tmp_path / "bench-junit.xml"
    arguments = [str(junit), str(missing), "--job", "Lint=success", "--job", "Test=failure"]
    assert main([*arguments, "--run-url", RUN_URL, "--commit", COMMIT]) == 0
    comment = capsys.readouterr().out
    assert comment.startswith(f"{MARKER}\n## Test results: 2 passed\n")
    assert f"No results from `{missing}`" in comment
    assert "| Test | ❌ failed |" in comment


def test_runs_as_a_module(tmp_path: Path) -> None:
    """Python -m tools.pr_comment works the way CI calls it."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.pr_comment",
            str(tmp_path / "junit.xml"),
            "--run-url",
            RUN_URL,
            "--commit",
            COMMIT,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.startswith(MARKER)


def test_a_triage_section_sits_between_the_results_and_the_jobs() -> None:
    """The triage verdict follows the test results, so a failed bench's cause is read first."""
    comment = render_comment(
        "## Test results: 1 failed\n",
        [JobResult("Bench", "failure")],
        RUN_URL,
        COMMIT,
        triage="### Triage of the failed bench run\n\nVerdict here\n",
    )
    results_at = comment.index("## Test results")
    triage_at = comment.index("### Triage of the failed bench run")
    jobs_at = comment.index("### CI jobs")
    assert results_at < triage_at < jobs_at


def test_main_includes_the_triage_file_when_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--triage reads the section the triage job wrote."""
    section = tmp_path / "triage.md"
    section.write_text("### Triage of the failed bench run\n\n`CABLE`\n", encoding="utf-8")
    arguments = [str(tmp_path / "junit.xml"), "--triage", str(section)]
    assert main([*arguments, "--run-url", RUN_URL, "--commit", COMMIT]) == 0
    assert "`CABLE`" in capsys.readouterr().out


def test_main_says_when_the_triage_section_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A triage file asked for but not written means the job didn't finish; the comment says so."""
    arguments = [str(tmp_path / "junit.xml"), "--triage", str(tmp_path / "absent.md")]
    assert main([*arguments, "--run-url", RUN_URL, "--commit", COMMIT]) == 0
    comment = capsys.readouterr().out
    assert "### Triage of the failed bench run" in comment
    assert "the triage job didn't finish" in comment


def test_without_triage_the_comment_has_no_triage_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the bench passed, CI doesn't pass --triage and the comment doesn't mention it."""
    assert main([str(tmp_path / "junit.xml"), "--run-url", RUN_URL, "--commit", COMMIT]) == 0
    assert "Triage" not in capsys.readouterr().out
