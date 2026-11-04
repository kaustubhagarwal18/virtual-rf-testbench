"""Run the bench suite with a fault, and save the run with its known cause.

One run:      python -m tools.fault_run cable_loss --size 1.5 --out results/faults
The data set: python -m tools.fault_run --dataset triage/data/runs

A saved run is a folder with the suite's results.json, the instruments' logs.txt and a
separate label.json holding the cause. A run in which nothing failed isn't saved: it would
be a labelled failure without a failure.
"""

import argparse
import json
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Protocol

from tools.bench_control import BenchControlError, ComposeBench, PytestSuite
from tools.faults import CATALOGUE, Bench, Fault

PACKAGE_NAME = "virtual-rf-testbench"
LOG_LINES_KEPT = 300
"""The last lines of the instruments' logs kept with a run, to keep saved runs small."""


class ControllableBench(Bench, Protocol):
    """A bench the fault tooling can also read logs from."""

    def logs_since(self, since: datetime) -> str:
        """Return the instruments' logs from a moment on.

        Args:
            since: The moment.

        Returns:
            The log lines.
        """


class Suite(Protocol):
    """Runs the bench suite."""

    def run(self, results_path: Path, firmware_version: str) -> None:
        """Run it, writing the results file.

        Args:
            results_path: Where the results file goes.
            firmware_version: The release the bench runs.
        """


class NothingFailedError(Exception):
    """The fault was on, yet every test passed: the run has no failure to label."""


def run_fault(
    fault: Fault,
    size: float | None,
    run_folder: Path,
    *,
    bench: ControllableBench,
    suite: Suite,
    default_version: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, int]:
    """Switch a fault on, run the suite, restore the bench, and save the run with its label.

    Args:
        fault: The fault from the catalogue.
        size: Its size; None uses the fault's question size.
        run_folder: The new folder to save the run in; it must not exist yet.
        bench: The bench.
        suite: The bench suite.
        default_version: The bench's normal release, restored afterwards.
        clock: Tells the time, for reading the logs from the run's start.

    Returns:
        The run's totals: tests run, passed, failed, errors and skipped.

    Raises:
        NothingFailedError: If no test failed; the folder is removed again.
    """
    run_folder.mkdir(parents=True)
    started = clock()
    try:
        firmware_version = fault.apply(bench, size, default_version)
        suite.run(run_folder / "results.json", firmware_version)
        logs = bench.logs_since(started)
    finally:
        fault.restore(bench, default_version)
    totals: dict[str, int] = json.loads((run_folder / "results.json").read_text())["totals"]
    if not totals["failed"] and not totals["error"]:
        shutil.rmtree(run_folder)
        raise NothingFailedError(f"{fault.name} at size {size}: every test passed; not saved")
    kept_logs = logs.splitlines()[-LOG_LINES_KEPT:]
    (run_folder / "logs.txt").write_text("\n".join(kept_logs) + "\n", encoding="utf-8")
    label = {
        "fault": fault.name,
        "size": fault.question_size if size is None else size,
        "unit": fault.unit,
        **asdict(fault.label),
    }
    (run_folder / "label.json").write_text(json.dumps(label, indent=2) + "\n", encoding="utf-8")
    return totals


def build_dataset(
    root: Path, bench: ControllableBench, suite: Suite, default_version: str
) -> list[Path]:
    """Run every fault at its history sizes and its question size, replacing any older data set.

    Args:
        root: The data set's folder; its history/ and questions/ folders are replaced.
        bench: The bench.
        suite: The bench suite.
        default_version: The bench's normal release.

    Returns:
        Every saved run's folder.
    """
    for part in ("history", "questions"):
        shutil.rmtree(root / part, ignore_errors=True)
    saved: list[Path] = []
    for fault in CATALOGUE.values():
        for number, size in enumerate(fault.history_sizes, start=1):
            folder = root / "history" / f"{fault.name}-{number}"
            run_fault(
                fault, size, folder, bench=bench, suite=suite, default_version=default_version
            )
            saved.append(folder)
        folder = root / "questions" / fault.name
        run_fault(
            fault,
            fault.question_size,
            folder,
            bench=bench,
            suite=suite,
            default_version=default_version,
        )
        saved.append(folder)
    return saved


def main(
    arguments: Sequence[str] | None = None,
    bench: ControllableBench | None = None,
    suite: Suite | None = None,
) -> int:
    """Run one fault, or build the whole data set, from the command line.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        bench: The bench; None uses Docker Compose on this machine.
        suite: The bench suite; None runs it with pytest.

    Returns:
        0 on success, 1 if the bench couldn't be controlled or nothing failed.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tools.fault_run", description="Run the bench suite with a fault."
    )
    parser.add_argument("name", nargs="?", choices=sorted(CATALOGUE), help="the fault")
    parser.add_argument("--size", type=float, help="the fault's size; default: its question size")
    parser.add_argument("--out", type=Path, default=Path("results/faults"), help="where runs go")
    parser.add_argument("--dataset", type=Path, help="build the whole data set in this folder")
    parsed = parser.parse_args(arguments)
    if (parsed.name is None) == (parsed.dataset is None):
        parser.error("give a fault name or --dataset, not both")
    bench = bench or ComposeBench()
    suite = suite or PytestSuite()
    default_version = version(PACKAGE_NAME)
    try:
        if parsed.dataset is not None:
            saved = build_dataset(parsed.dataset, bench, suite, default_version)
            sys.stdout.write(f"Saved {len(saved)} runs in {parsed.dataset}\n")
            return 0
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        folder = parsed.out / f"{parsed.name}-{stamp}"
        totals = run_fault(
            CATALOGUE[parsed.name],
            parsed.size,
            folder,
            bench=bench,
            suite=suite,
            default_version=default_version,
        )
    except (BenchControlError, NothingFailedError) as error:
        sys.stdout.write(f"{error}\n")
        return 1
    sys.stdout.write(
        f"Saved {folder}: {totals['failed']} failed, {totals['error']} errors, "
        f"{totals['passed']} passed\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
