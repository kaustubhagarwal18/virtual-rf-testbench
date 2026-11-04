"""The JSON results file every bench run writes: what ran, what failed, and the evidence.

It holds what a real bench knows after a run, and nothing more: the firmware versions, which
tests were selected, each test's outcome and failure message, the instrument errors in that
message, and every measurement with its limit and margin. It never holds the cause of a
failure; fault runs keep that in a separate label file.

tests/bench/conftest.py feeds pytest's reports to a ResultsCollector and writes the file when
the session ends, to BENCH_RESULTS_JSON or reports/bench-results.json.
"""

import json
import re
import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pytest

DEFAULT_RESULTS_PATH = Path("reports/bench-results.json")
RESULTS_PATH_VARIABLE = "BENCH_RESULTS_JSON"
MEASUREMENT_PREFIX = "measurement:"
"""How record_measurements names each measurement's JUnit property."""

_INSTRUMENT_ERROR = re.compile(r'-\d{3},"[^"]*"')
"""An SCPI error as instruments report it, for example -113,"Undefined header"."""

_IDENTITY_FIELDS = 4
"""*IDN? answers manufacturer, model, serial number and firmware version."""

_OUTCOME_ORDER = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
"""When a test's phases disagree, the worse outcome wins."""


class Report(Protocol):
    """The parts of pytest's TestReport the collector reads."""

    @property
    def nodeid(self) -> str:
        """The test's id."""

    @property
    def when(self) -> str:
        """The phase: setup, call or teardown."""

    @property
    def outcome(self) -> str:
        """passed, failed or skipped."""

    @property
    def duration(self) -> float:
        """The phase's time in seconds."""

    @property
    def longrepr(self) -> object:
        """The failure or skip details."""

    @property
    def user_properties(self) -> list[tuple[str, object]]:
        """Properties recorded with record_property."""

    @property
    def longreprtext(self) -> str:
        """The failure or skip text, empty for a pass."""


@dataclass
class TestRecord:
    """One test's result, built from the reports of its setup, call and teardown.

    Attributes:
        test_id: The pytest node id.
        outcome: passed, failed, error (setup or teardown failed) or skipped.
        duration_s: The time of all its phases.
        message: The failure or skip message, empty for a pass.
        instrument_errors: SCPI errors found in the failure text, in order, without repeats.
        measurements: Each recorded measurement as a dict, with limit and margin.
    """

    __test__ = False

    test_id: str
    outcome: str = "passed"
    duration_s: float = 0.0
    message: str = ""
    instrument_errors: list[str] = field(default_factory=list)
    measurements: list[dict[str, Any]] = field(default_factory=list)


def instrument_errors_in(text: str) -> list[str]:
    """Find the SCPI errors an instrument reported, in a failure's text.

    Args:
        text: The failure text, traceback included.

    Returns:
        Each error once, for example '-113,"Undefined header"', in the order first seen.
    """
    return list(dict.fromkeys(_INSTRUMENT_ERROR.findall(text)))


def measurements_in(user_properties: Iterable[tuple[str, object]]) -> list[dict[str, Any]]:
    """Read the measurements record_measurements stored as test properties.

    Args:
        user_properties: A report's (name, value) properties.

    Returns:
        Each measurement's fields; other properties are ignored.
    """
    return [
        json.loads(str(value))
        for name, value in user_properties
        if name.startswith(MEASUREMENT_PREFIX)
    ]


def _outcome_of(report: Report) -> str:
    """Name a report's outcome, calling a failed setup or teardown an error, as pytest does.

    Args:
        report: One phase's report.

    Returns:
        passed, failed, error or skipped.
    """
    if report.outcome == "failed" and report.when != "call":
        return "error"
    return report.outcome


def _message_of(report: Report) -> str:
    """Return a report's short failure or skip message.

    Args:
        report: One phase's report.

    Returns:
        The message pytest shows for it in the summary.
    """
    crash = getattr(report.longrepr, "reprcrash", None)
    if crash is not None:
        return str(crash.message)
    if isinstance(report.longrepr, tuple):
        return str(report.longrepr[-1])
    return report.longreprtext.strip().splitlines()[-1] if report.longreprtext.strip() else ""


class ResultsCollector:
    """Collects one bench session's results and writes them as JSON."""

    def __init__(self, command: Sequence[str]) -> None:
        """Start an empty record of a session.

        Args:
            command: The command line the session was started with.
        """
        self.started = datetime.now(UTC)
        self.command = shlex.join(command)
        self.firmware_versions: dict[str, str] = {}
        self.deselected = 0
        self.selected = 0
        self._tests: dict[str, TestRecord] = {}

    def add_deselected(self, count: int) -> None:
        """Count tests that were collected but deselected, for example with -m or -k.

        Args:
            count: How many were deselected.
        """
        self.deselected += count

    def add_report(self, report: Report) -> None:
        """Add one phase's report to its test's record.

        Args:
            report: The setup, call or teardown report.
        """
        record = self._tests.setdefault(report.nodeid, TestRecord(report.nodeid))
        record.duration_s += report.duration
        outcome = _outcome_of(report)
        if _OUTCOME_ORDER[outcome] > _OUTCOME_ORDER[record.outcome]:
            record.outcome = outcome
            record.message = _message_of(report)
            record.instrument_errors = instrument_errors_in(report.longreprtext)
        if report.when == "call":
            record.measurements = measurements_in(report.user_properties)

    def to_dict(self) -> dict[str, Any]:
        """Build the results file's content.

        Returns:
            The run's identity, selection, firmware versions, totals and per-test records.
        """
        finished = datetime.now(UTC)
        tests = list(self._tests.values())
        totals = {outcome: 0 for outcome in _OUTCOME_ORDER}
        for test in tests:
            totals[test.outcome] += 1
        return {
            "run_id": self.started.strftime("%Y%m%dT%H%M%S.%fZ"),
            "started": self.started.isoformat(timespec="seconds"),
            "duration_s": round((finished - self.started).total_seconds(), 3),
            "firmware_versions": dict(self.firmware_versions),
            "selection": {
                "command": self.command,
                "collected": self.selected + self.deselected,
                "selected": self.selected,
                "deselected": self.deselected,
            },
            "totals": {"run": len(tests), **totals},
            "tests": [
                {
                    "id": test.test_id,
                    "outcome": test.outcome,
                    "duration_s": round(test.duration_s, 3),
                    "message": test.message,
                    "instrument_errors": test.instrument_errors,
                    "measurements": test.measurements,
                }
                for test in tests
            ],
        }

    def write(self, path: Path) -> None:
        """Write the results file, creating its folder if needed.

        Args:
            path: Where to write it.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")


def firmware_version_from_identity(identity: str) -> str:
    """Read the firmware version from an *IDN? reply.

    Args:
        identity: The reply, for example 'VirtualRF,SG-1,100001,0.1.0'.

    Returns:
        The fourth field, or 'unknown' if the reply has fewer fields.
    """
    fields = identity.split(",")
    return fields[_IDENTITY_FIELDS - 1].strip() if len(fields) >= _IDENTITY_FIELDS else "unknown"


class ResultsFilePlugin:
    """Pytest hooks that feed a ResultsCollector and write its file when the session ends."""

    name = "bench-results"
    """The name the plugin is registered under, so fixtures can find it."""

    def __init__(self, collector: ResultsCollector, path: Path) -> None:
        """Remember what to collect into and where to write it.

        Args:
            collector: The session's collector.
            path: Where to write the results file.
        """
        self.collector = collector
        self.path = path

    def pytest_deselected(self, items: Sequence[pytest.Item]) -> None:
        """Count deselected tests.

        Args:
            items: The tests deselected, for example by -m or -k.
        """
        self.collector.add_deselected(len(items))

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Count the tests that will run.

        Args:
            session: The session, whose items are the selected tests.
        """
        self.collector.selected = len(session.items)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Add each phase's report.

        Args:
            report: The setup, call or teardown report.
        """
        self.collector.add_report(report)

    def pytest_sessionfinish(self) -> None:
        """Write the results file."""
        self.collector.write(self.path)
