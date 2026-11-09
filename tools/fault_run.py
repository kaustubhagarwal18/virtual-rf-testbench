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
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from tests.utilities.sequencer import SEQUENCE_LOG_VARIABLE
from tools.bench_control import BenchControlError, ComposeBench, PytestSuite
from tools.faults import CATALOGUE, Bench, Fault

PACKAGE_NAME = "virtual-rf-testbench"
LOG_LINES_KEPT = 300
"""The last lines of the instruments' logs kept with a run, to keep saved runs small."""

HISTORY = "history"
"""The data set's past failures, which triage may retrieve with their labels."""

QUESTIONS = "questions"
"""The data set's questions, which triage is asked about and never sees the labels of."""

STAGING = ".building"
"""Where a new data set is built, so a failure part-way can't destroy the previous one."""

USABLE_EXIT_STATUSES = frozenset({0, 1})
"""Pytest's exit status for 'all passed' and 'tests failed'; any other means a broken run."""


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

    def run(
        self,
        results_path: Path,
        firmware_version: str,
        environment: Mapping[str, str] = MappingProxyType({}),
        selection: Sequence[str] = (),
    ) -> None:
        """Run it, writing the results file.

        Args:
            results_path: Where the results file goes.
            firmware_version: The release the bench runs.
            environment: Extra environment for the run.
            selection: Extra pytest arguments.
        """


class NothingFailedError(Exception):
    """The fault was on, yet every test passed: the run has no failure to label."""


def try_restore(fault: Fault, bench: Bench, default_version: str) -> None:
    """Switch the fault off, reporting a failure instead of raising it.

    It is called from a finally block, so it must never raise: a restore that failed would
    otherwise replace the failure that caused it, and the real cause would be lost.

    Args:
        fault: The fault to switch off.
        bench: The bench.
        default_version: The bench's normal release.
    """
    try:
        fault.restore(bench, default_version)
    except BenchControlError as error:
        sys.stdout.write(
            f"warning: couldn't switch {fault.name} off, so the bench may still carry it: {error}\n"
        )


class UnusableRunError(Exception):
    """The suite didn't produce a usable run, so its evidence is kept instead of deleted."""


def unusable_reason(results: dict[str, Any]) -> str | None:
    """Say why a run isn't usable, if it isn't; such a run must not look like 'nothing failed'.

    Args:
        results: The suite's results file, already read.

    Returns:
        Why the run can't be used, or None when it can.
    """
    collection_errors = results.get("collection_errors") or []
    if collection_errors:
        first = collection_errors[0]
        return f"pytest couldn't collect {first['id']}: {first['message']}"
    exit_status = results.get("exit_status")
    if exit_status not in USABLE_EXIT_STATUSES:
        return f"pytest ended with exit status {exit_status}, so the run is incomplete"
    return None


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
        size: Its size; None uses the fault's first question size.
        run_folder: The new folder to save the run in; it must not exist yet.
        bench: The bench.
        suite: The bench suite.
        default_version: The bench's normal release, restored afterwards.
        clock: Tells the time, for reading the logs from the run's start.

    Returns:
        The run's totals: tests run, passed, failed, errors and skipped.

    Raises:
        NothingFailedError: If no test failed; the folder is removed again.
        UnusableRunError: If pytest couldn't finish the run; the folder is kept.
    """
    run_folder.mkdir(parents=True)
    started = clock()
    try:
        firmware_version = fault.apply(bench, size, default_version)
        environment = {
            **fault.suite_environment(size),
            SEQUENCE_LOG_VARIABLE: str(run_folder / "sequence.log"),
        }
        suite.run(run_folder / "results.json", firmware_version, environment, fault.selection)
        logs = bench.logs_since(started)
    finally:
        try_restore(fault, bench, default_version)
    results: dict[str, Any] = json.loads((run_folder / "results.json").read_text())
    if (problem := unusable_reason(results)) is not None:
        raise UnusableRunError(f"{problem}; kept {run_folder}")
    totals: dict[str, int] = results["totals"]
    if not totals["failed"] and not totals["error"]:
        shutil.rmtree(run_folder)
        raise NothingFailedError(f"{fault.name} at size {size}: every test passed; not saved")
    save_logs(run_folder, logs)
    save_label(run_folder, fault, size)
    return totals


def save_logs(run_folder: Path, logs: str) -> None:
    """Keep the last lines of the instruments' logs with the run.

    Args:
        run_folder: Where the run is saved.
        logs: Everything both instruments logged during the run.
    """
    kept = logs.splitlines()[-LOG_LINES_KEPT:]
    (run_folder / "logs.txt").write_text("\n".join(kept) + "\n", encoding="utf-8")


def save_label(run_folder: Path, fault: Fault, size: float | None) -> None:
    """Write the run's known cause beside its evidence, in a file of its own.

    Args:
        run_folder: Where the run is saved.
        fault: The fault that was injected.
        size: Its size, or None for a fault that has none.
    """
    label = {
        "fault": fault.name,
        "size": fault.question_sizes[0] if size is None else size,
        "unit": fault.unit,
        **asdict(fault.label),
    }
    (run_folder / "label.json").write_text(json.dumps(label, indent=2) + "\n", encoding="utf-8")


def dataset_plan() -> Iterator[tuple[Path, Fault, float | None]]:
    """List every run the data set holds: three history and three question runs per fault.

    Yields:
        tuple[Path, Fault, float | None]: Each run's folder relative to the data set's root,
        its fault, and its size.
    """
    for fault in CATALOGUE.values():
        for number, size in enumerate(fault.history_sizes, start=1):
            yield Path(HISTORY) / f"{fault.name}-{number}", fault, size
        for number, size in enumerate(fault.question_sizes, start=1):
            yield Path(QUESTIONS) / f"{fault.name}-{number}", fault, size


def build_dataset(
    root: Path, bench: ControllableBench, suite: Suite, default_version: str
) -> list[Path]:
    """Run every fault at its history and question sizes, replacing any older data set.

    The runs are built in a staging folder and only moved into place once every one of them
    has been saved, so a bench that fails part-way leaves the previous data set untouched.
    A run pytest couldn't finish keeps its evidence, so the staging folder is kept too, and
    the next build clears it.

    Args:
        root: The data set's folder.
        bench: The bench.
        suite: The bench suite.
        default_version: The bench's normal release.

    Returns:
        Every saved run's folder.

    Raises:
        UnusableRunError: Re-raised after keeping the staging folder, so the evidence of the
            run pytest couldn't finish survives for inspection.
    """
    staging = root / STAGING
    shutil.rmtree(staging, ignore_errors=True)
    completed = False
    keep_evidence = False
    try:
        for relative, fault, size in dataset_plan():
            run_fault(
                fault,
                size,
                staging / relative,
                bench=bench,
                suite=suite,
                default_version=default_version,
            )
        completed = True
    except UnusableRunError:
        keep_evidence = True
        raise
    finally:
        if not completed and not keep_evidence:
            shutil.rmtree(staging, ignore_errors=True)
    for part in (HISTORY, QUESTIONS):
        shutil.rmtree(root / part, ignore_errors=True)
        (staging / part).rename(root / part)
    shutil.rmtree(staging, ignore_errors=True)
    return [root / relative for relative, _fault, _size in dataset_plan()]


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
    except (BenchControlError, NothingFailedError, UnusableRunError) as error:
        sys.stdout.write(f"{error}\n")
        return 1
    sys.stdout.write(
        f"Saved {folder}: {totals['failed']} failed, {totals['error']} errors, "
        f"{totals['passed']} passed\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
