"""Stand-ins for the Docker bench and the bench suite, for the fault tooling's tests."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from tools.bench_control import BenchControlError

FAILING_TOTALS = {"run": 7, "passed": 5, "skipped": 0, "failed": 2, "error": 0}
PASSING_TOTALS = {"run": 7, "passed": 7, "skipped": 0, "failed": 0, "error": 0}


class FakeBench:
    """Records admin commands and restarts, and returns fixed logs."""

    def __init__(
        self, logs: str = "generator-1  | {}\n", *, clear_fails_after: int | None = None
    ) -> None:
        """Start with no calls recorded.

        Args:
            logs: What logs_since returns.
            clear_fails_after: Fail every CLEAR after this many have succeeded, so a test can
                let a fault be applied and then fail while it is switched off again.
        """
        self.calls: list[tuple[str, str]] = []
        self.logs = logs
        self.clear_fails_after = clear_fails_after
        self.clears = 0

    def send_admin(self, instrument: str, line: str) -> dict[str, Any]:
        """Record an admin command.

        Raises:
            BenchControlError: If this bench was built with failing CLEARs.
        """
        self.calls.append((instrument, line))
        if line == "CLEAR":
            self.clears += 1
            if self.clear_fails_after is not None and self.clears > self.clear_fails_after:
                raise BenchControlError(f"the {instrument} stopped answering")
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
        unusable_after: int | None = None,
    ) -> None:
        """Choose what the suite writes, or how it fails.

        Args:
            totals: The totals to write; failing totals by default.
            error: Raised instead of writing anything.
            exit_status: Pytest's exit status; derived from the totals when None.
            collection_errors: Collection errors to write, as a suite that couldn't collect.
            fail_after: Fail once this many runs have been written.
            unusable_after: Write a run pytest couldn't finish, once this many are written.
        """
        chosen_totals = FAILING_TOTALS if totals is None else totals
        failed = chosen_totals["failed"] or chosen_totals["error"]
        self.results: dict[str, object] = {
            "exit_status": (1 if failed else 0) if exit_status is None else exit_status,
            "collection_errors": list(collection_errors),
            "totals": chosen_totals,
        }
        self.error = error
        self.fail_after = fail_after
        self.unusable_after = unusable_after
        self.versions: list[str] = []
        self.environments: list[dict[str, str]] = []
        self.selections: list[list[str]] = []

    def run(
        self,
        results_path: Path,
        firmware_version: str,
        environment: Mapping[str, str] = MappingProxyType({}),
        selection: Sequence[str] = (),
    ) -> None:
        """Write the results file, or fail the way the real suite can.

        Args:
            results_path: Where the results file goes.
            firmware_version: The release the bench runs.
            environment: Extra environment for the run, recorded for the test to check.
            selection: Extra pytest arguments, recorded for the test to check.

        Raises:
            BenchControlError: Once fail_after runs have been written.
        """
        self.versions.append(firmware_version)
        self.environments.append(dict(environment))
        self.selections.append(list(selection))
        if self.error is not None:
            raise self.error
        if self.fail_after is not None and len(self.versions) > self.fail_after:
            raise BenchControlError("the bench stopped answering")
        unusable = self.unusable_after is not None and len(self.versions) > self.unusable_after
        results = {
            **self.results,
            "firmware_versions": {"generator": firmware_version},
        }
        if unusable:
            results["exit_status"] = 2
            results["collection_errors"] = [
                {"id": "tests/bench/test_bench.py", "message": "ImportError"}
            ]
        results_path.write_text(json.dumps(results), encoding="utf-8")
