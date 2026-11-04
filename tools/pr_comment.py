"""Build the pull request comment CI posts: the test results summary and every CI job's result.

CI runs it once all other jobs have finished, then posts the output as one comment, or
updates its earlier comment, found by the marker on the first line:

    python -m tools.pr_comment reports/junit.xml reports/bench-junit.xml --job Lint=success ...
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from tools.results_summary import add_result_arguments, summarise_files

MARKER = "<!-- virtual-rf-testbench:test-results -->"
"""The first line of every results comment, so CI can find and update its own comment."""

_JOB_RESULT_LABELS = {
    "success": "✅ passed",
    "failure": "❌ failed",
    "cancelled": "⚪ cancelled",
    "skipped": "⏭️ skipped",
}
"""How each GitHub Actions job result is shown."""


@dataclass(frozen=True)
class JobResult:
    """One CI job and how it ended.

    Attributes:
        name: The job's name, for example 'Lint'.
        result: The GitHub Actions result: success, failure, cancelled or skipped.
    """

    name: str
    result: str


def parse_job(text: str) -> JobResult:
    """Read a job argument of the form NAME=RESULT.

    Args:
        text: The argument, for example 'Lint=success'.

    Returns:
        The job and its result.

    Raises:
        argparse.ArgumentTypeError: If there is no '=', or the name or result is empty.
    """
    name, separator, result = text.partition("=")
    if not separator or not name.strip() or not result.strip():
        raise argparse.ArgumentTypeError(f"expected NAME=RESULT, got {text!r}")
    return JobResult(name.strip(), result.strip())


def render_comment(summary: str, jobs: Sequence[JobResult], run_url: str, commit: str) -> str:
    """Write the comment: marker, test results, a table of CI jobs and a link to the run.

    Args:
        summary: The Markdown test results summary.
        jobs: Every CI job the comment reports, in order.
        run_url: The CI run's page.
        commit: The commit the run tested.

    Returns:
        The comment's Markdown.
    """
    lines = [MARKER, summary.rstrip("\n"), "", "### CI jobs", ""]
    lines += ["| Job | Result |", "|---|---|"]
    lines += [f"| {job.name} | {_job_label(job.result)} |" for job in jobs]
    lines += ["", f"Commit `{commit[:7]}` · [CI run]({run_url})"]
    return "\n".join(lines) + "\n"


def _job_label(result: str) -> str:
    """Show a job result with a symbol, so failures stand out.

    Args:
        result: The GitHub Actions result.

    Returns:
        The labelled result, or the raw result if it isn't a known one.
    """
    return _JOB_RESULT_LABELS.get(result, result)


def main(arguments: Sequence[str] | None = None) -> int:
    """Print the pull request comment.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.

    Returns:
        0; missing results files are named in the comment rather than failing.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tools.pr_comment", description="Build the CI results comment."
    )
    add_result_arguments(parser)
    parser.add_argument(
        "--job", type=parse_job, action="append", default=[], help="a job's NAME=RESULT"
    )
    parser.add_argument("--run-url", required=True, help="the CI run's page")
    parser.add_argument("--commit", required=True, help="the commit the run tested")
    parsed = parser.parse_args(arguments)
    summary = summarise_files(parsed.junit, parsed.coverage, parsed.coverage_floor)
    sys.stdout.write(render_comment(summary, parsed.job, parsed.run_url, parsed.commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
