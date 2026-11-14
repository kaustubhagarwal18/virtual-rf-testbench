"""Tests for the CI workflow's job graph: what gates publishing, and what the comment reports."""

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
"""The CI workflow these tests read."""

PUBLISH_GATES = {"lint", "test", "secrets", "image", "bench", "triage-image", "triage-smoke"}
"""Every job that must pass before an image is pushed."""

UNREPORTED = {"publish", "report"}
"""The jobs the results comment doesn't list: publishing runs on main only, and the report is
the comment itself."""


@pytest.fixture(scope="module")
def jobs() -> dict[str, Any]:
    """Return the workflow's jobs, by id."""
    parsed = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    found: dict[str, Any] = parsed["jobs"]
    return found


def _needs(job: dict[str, Any]) -> set[str]:
    """Read a job's needs as a set, whether written as one id or a list.

    Args:
        job: The job's definition.

    Returns:
        The ids it needs.
    """
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def test_publish_needs_exactly_the_gates(jobs: dict[str, Any]) -> None:  # pylint: disable=redefined-outer-name
    """Publishing waits for every check and both images' tests, and for nothing else."""
    assert _needs(jobs["publish"]) == PUBLISH_GATES


def test_the_advisory_triage_job_never_gates_publishing(jobs: dict[str, Any]) -> None:  # pylint: disable=redefined-outer-name
    """The bench triage is allowed to fail, so publish never needs it."""
    assert "triage" not in _needs(jobs["publish"])
    assert jobs["triage"]["continue-on-error"] is True


def test_the_report_runs_unless_cancelled(jobs: dict[str, Any]) -> None:  # pylint: disable=redefined-outer-name
    """The results comment is posted even when a job it needs failed."""
    assert "!cancelled()" in jobs["report"]["if"]


def test_the_report_needs_every_job_it_reports(jobs: dict[str, Any]) -> None:  # pylint: disable=redefined-outer-name
    """A new job can't be left out of the comment: report needs every job but itself and publish."""
    assert _needs(jobs["report"]) == set(jobs) - UNREPORTED


def test_the_container_tests_need_the_triage_image(jobs: dict[str, Any]) -> None:  # pylint: disable=redefined-outer-name
    """The container tests run the image the triage-image job built; they build nothing."""
    assert "triage-image" in _needs(jobs["triage-smoke"])
    commands = " ".join(step.get("run", "") for step in jobs["triage-smoke"]["steps"])
    assert "docker build" not in commands
    assert "make triage-image" not in commands
