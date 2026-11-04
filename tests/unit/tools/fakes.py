"""Stand-ins for the Docker bench and the bench suite, for the fault tooling's tests."""

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.bench_control import BenchControlError

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
        self,
        totals: dict[str, int] | None = None,
        error: Exception | None = None,
        *,
        exit_status: int | None = None,
        collection_errors: Sequence[dict[str, str]] = (),
        fail_after: int | None = None,
    ) -> None:
        """Choose what the suite writes, or how it fails.

        Args:
            totals: The totals to write; failing totals by default.
            error: Raised instead of writing anything.
            exit_status: Pytest's exit status; derived from the totals when None.
            collection_errors: Collection errors to write, as a suite that couldn't collect.
            fail_after: Fail once this many runs have been written.
        """
        self.totals = FAILING_TOTALS if totals is None else totals
        self.error = error
        failed = self.totals["failed"] or self.totals["error"]
        self.exit_status = (1 if failed else 0) if exit_status is None else exit_status
        self.collection_errors = list(collection_errors)
        self.fail_after = fail_after
        self.versions: list[str] = []

    def run(self, results_path: Path, firmware_version: str) -> None:
        """Write the results file, or fail the way the real suite can.

        Args:
            results_path: Where the results file goes.
            firmware_version: The release the bench runs.

        Raises:
            BenchControlError: Once fail_after runs have been written.
        """
        self.versions.append(firmware_version)
        if self.error is not None:
            raise self.error
        if self.fail_after is not None and len(self.versions) > self.fail_after:
            raise BenchControlError("the bench stopped answering")
        results = {
            "firmware_versions": {"generator": firmware_version},
            "exit_status": self.exit_status,
            "collection_errors": self.collection_errors,
            "totals": self.totals,
        }
        results_path.write_text(json.dumps(results), encoding="utf-8")
