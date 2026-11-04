"""Stand-ins for the Docker bench and the bench suite, for the fault tooling's tests."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

FAILING_TOTALS = {"run": 7, "passed": 5, "skipped": 0, "failed": 2, "error": 0}
PASSING_TOTALS = {"run": 7, "passed": 7, "skipped": 0, "failed": 0, "error": 0}


class FakeBench:
    """Records admin commands and restarts, and returns fixed logs."""

    def __init__(self, logs: str = "generator-1  | {}\n") -> None:
        """Start with no calls recorded."""
        self.calls: list[tuple[str, str]] = []
        self.logs = logs

    def send_admin(self, instrument: str, line: str) -> dict[str, Any]:
        """Record an admin command."""
        self.calls.append((instrument, line))
        return {}

    def start(self, firmware_version: str) -> None:
        """Record a restart as a release."""
        self.calls.append(("start", firmware_version))

    def logs_since(self, since: datetime) -> str:
        """Return the fixed logs."""
        del since
        return self.logs


class FakeSuite:
    """Writes a results file with chosen totals, recording the release it was told to expect."""

    def __init__(
        self, totals: dict[str, int] | None = None, error: Exception | None = None
    ) -> None:
        """Choose the totals to write, or an error to raise instead."""
        self.totals = FAILING_TOTALS if totals is None else totals
        self.error = error
        self.versions: list[str] = []

    def run(self, results_path: Path, firmware_version: str) -> None:
        """Write the results file, or raise the chosen error."""
        self.versions.append(firmware_version)
        if self.error is not None:
            raise self.error
        results = {"firmware_versions": {"generator": firmware_version}, "totals": self.totals}
        results_path.write_text(json.dumps(results), encoding="utf-8")
