"""Tests for saving CI's failed bench run in the folder layout the triage assistant reads."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.fault_run import LOG_LINES_KEPT
from tools.save_failed_run import main, save_failed_run
from triage.evidence import SavedRun

RESULTS = {"totals": {"failed": 1, "error": 0}, "tests": [], "exit_status": 1}


def write_reports(folder: Path, log_lines: int = 5, sequence: bool = True) -> Path:
    """Write the reports a CI bench job leaves behind, and return their folder."""
    folder.mkdir(parents=True)
    (folder / "bench-results.json").write_text(json.dumps(RESULTS), encoding="utf-8")
    logs = [f"generator-1 | line {number}" for number in range(log_lines)]
    (folder / "bench-logs.txt").write_text("\n".join(logs) + "\n", encoding="utf-8")
    if sequence:
        (folder / "sequence.log").write_text("*RST\n*OPC?\n", encoding="utf-8")
    return folder


def test_the_saved_run_has_results_logs_and_sequence_but_no_label(tmp_path: Path) -> None:
    """The run is saved as fault_run saves one, minus the label: the cause is the question."""
    reports = write_reports(tmp_path / "reports")
    out = tmp_path / "failed-run"
    save_failed_run(
        reports / "bench-results.json", reports / "bench-logs.txt", reports / "sequence.log", out
    )
    assert json.loads((out / "results.json").read_text(encoding="utf-8")) == RESULTS
    assert (out / "sequence.log").read_text(encoding="utf-8") == "*RST\n*OPC?\n"
    assert (out / "logs.txt").read_text(encoding="utf-8").splitlines()[0] == "generator-1 | line 0"
    assert not (out / "label.json").exists()
    assert SavedRun.load(out).results == RESULTS


@pytest.mark.parametrize("log_lines", [0, 1, LOG_LINES_KEPT, LOG_LINES_KEPT + 1, 2000])
def test_only_the_last_lines_of_the_logs_are_kept(tmp_path: Path, log_lines: int) -> None:
    """Long logs are cut to the same tail fault_run keeps, so CI runs look like history runs."""
    reports = write_reports(tmp_path / "reports", log_lines=log_lines)
    out = tmp_path / "failed-run"
    save_failed_run(
        reports / "bench-results.json", reports / "bench-logs.txt", reports / "sequence.log", out
    )
    kept = [line for line in (out / "logs.txt").read_text(encoding="utf-8").splitlines() if line]
    assert len(kept) == min(log_lines, LOG_LINES_KEPT)
    if log_lines:
        assert kept[-1] == f"generator-1 | line {log_lines - 1}"


def test_a_run_without_a_sequence_log_or_logs_is_still_saved(tmp_path: Path) -> None:
    """The sequence log and the logs are optional: a failure before either is still a failure."""
    reports = write_reports(tmp_path / "reports", sequence=False)
    (reports / "bench-logs.txt").unlink()
    out = tmp_path / "failed-run"
    save_failed_run(
        reports / "bench-results.json", reports / "bench-logs.txt", reports / "sequence.log", out
    )
    assert not (out / "sequence.log").exists()
    assert (out / "logs.txt").read_text(encoding="utf-8") == "\n"


def test_main_saves_the_run_from_its_arguments(tmp_path: Path) -> None:
    """The command line names the three reports and the folder to write."""
    reports = write_reports(tmp_path / "reports")
    out = tmp_path / "failed-run"
    code = main(
        [
            str(reports / "bench-results.json"),
            "--logs",
            str(reports / "bench-logs.txt"),
            "--sequence",
            str(reports / "sequence.log"),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert (out / "results.json").is_file()


def test_main_says_in_one_line_when_there_are_no_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a results file the bench failed before the suite ran; say so, save nothing."""
    out = tmp_path / "failed-run"
    code = main([str(tmp_path / "missing.json"), "--out", str(out)])
    assert code == 1
    message = capsys.readouterr().out
    assert len(message.splitlines()) == 1
    assert "missing.json" in message
    assert not out.exists()


def test_runs_as_a_module(tmp_path: Path) -> None:
    """Python -m tools.save_failed_run works the way CI calls it."""
    completed = subprocess.run(
        [sys.executable, "-m", "tools.save_failed_run", str(tmp_path / "missing.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "No run to save" in completed.stdout
