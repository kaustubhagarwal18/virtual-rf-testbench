"""Save CI's failed bench run in the folder layout the triage assistant reads.

The bench job leaves the suite's results, the instruments' logs and the sequence log in
reports/. This copies them into one run folder, laid out as tools.fault_run saves a run but
without a label: the cause is what the triage job is asked.

    python -m tools.save_failed_run reports/bench-results.json --logs reports/bench-logs.txt \
        --sequence reports/sequence.log --out reports/failed-run
"""

import argparse
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.fault_run import save_logs

DEFAULT_OUT = Path("reports/failed-run")
"""Where the run folder is written when --out isn't given."""


def save_failed_run(results: Path, logs: Path, sequence: Path, run_folder: Path) -> None:
    """Copy one bench run's reports into a run folder.

    Args:
        results: The suite's results file; it must exist.
        logs: Both instruments' logs; a missing file is saved as no logs.
        sequence: The sequence log; left out when missing.
        run_folder: The folder to create.
    """
    run_folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(results, run_folder / "results.json")
    if sequence.is_file():
        shutil.copyfile(sequence, run_folder / "sequence.log")
    text = logs.read_text(encoding="utf-8", errors="replace") if logs.is_file() else ""
    save_logs(run_folder, text)


def main(arguments: Sequence[str] | None = None) -> int:
    """Save the failed run named on the command line.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.

    Returns:
        0 when the run was saved; 1 when there is no results file, which means the bench
        failed before the suite could write one.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tools.save_failed_run",
        description="Save a failed bench run for the triage assistant.",
    )
    parser.add_argument("results", type=Path, help="the suite's results file")
    parser.add_argument("--logs", type=Path, default=Path("reports/bench-logs.txt"))
    parser.add_argument("--sequence", type=Path, default=Path("reports/sequence.log"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the run folder to write")
    parsed = parser.parse_args(arguments)
    if not parsed.results.is_file():
        sys.stdout.write(
            f"No run to save: {parsed.results} doesn't exist, so the bench failed before "
            "the suite wrote its results.\n"
        )
        return 1
    save_failed_run(parsed.results, parsed.logs, parsed.sequence, parsed.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
