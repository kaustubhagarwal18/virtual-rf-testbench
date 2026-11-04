"""Tests for running the suite with a fault and saving the run with its label."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.tools.fakes import FAILING_TOTALS, PASSING_TOTALS, FakeBench, FakeSuite
from tools.bench_control import BenchControlError
from tools.fault_run import (
    LOG_LINES_KEPT,
    NothingFailedError,
    UnusableRunError,
    build_dataset,
    main,
    run_fault,
)
from tools.faults import ANALYZER, CATALOGUE, GENERATOR

DEFAULT_VERSION = "0.1.0"
USAGE_ERROR_EXIT_CODE = 2


def test_a_saved_run_keeps_results_logs_and_a_separate_label(tmp_path: Path) -> None:
    """A run keeps results.json, logs.txt and label.json; the results never hold the label."""
    folder = tmp_path / "cable_loss-1"
    totals = run_fault(
        CATALOGUE["cable_loss"],
        1.2,
        folder,
        bench=FakeBench(),
        suite=FakeSuite(),
        default_version=DEFAULT_VERSION,
    )
    assert totals == FAILING_TOTALS
    assert sorted(path.name for path in folder.iterdir()) == [
        "label.json",
        "logs.txt",
        "results.json",
    ]
    label = json.loads((folder / "label.json").read_text())
    assert (label["fault"], label["size"], label["unit"], label["category"]) == (
        "cable_loss",
        1.2,
        "dB",
        "bench",
    )
    results_text = (folder / "results.json").read_text()
    assert "cable_loss" not in results_text
    assert "bench" not in results_text


def test_the_suite_expects_the_release_the_fault_runs(tmp_path: Path) -> None:
    """For a release fault the suite expects that release; otherwise the normal one."""
    suite = FakeSuite()
    run_fault(
        CATALOGUE["output_command_change"],
        None,
        tmp_path / "a",
        bench=FakeBench(),
        suite=suite,
        default_version="0.1.0",
    )
    run_fault(
        CATALOGUE["cable_loss"],
        None,
        tmp_path / "b",
        bench=FakeBench(),
        suite=suite,
        default_version="0.1.0",
    )
    assert suite.versions == ["0.2.0-rc1", "0.1.0"]


def test_the_bench_is_restored_even_when_the_suite_fails_to_run(tmp_path: Path) -> None:
    """If running the suite raises, the fault is still switched off."""
    bench = FakeBench()
    with pytest.raises(BenchControlError):
        run_fault(
            CATALOGUE["cable_loss"],
            1.0,
            tmp_path / "run",
            bench=bench,
            suite=FakeSuite(error=BenchControlError("no bench")),
            default_version=DEFAULT_VERSION,
        )
    assert bench.calls[-2:] == [(GENERATOR, "CLEAR"), (ANALYZER, "CLEAR")]


def test_a_run_where_nothing_failed_is_not_saved(tmp_path: Path) -> None:
    """With every test passing, the folder is removed and the caller is told."""
    folder = tmp_path / "too-small"
    with pytest.raises(NothingFailedError, match="every test passed"):
        run_fault(
            CATALOGUE["cable_loss"],
            0.1,
            folder,
            bench=FakeBench(),
            suite=FakeSuite(PASSING_TOTALS),
            default_version="0.1.0",
        )
    assert not folder.exists()


def test_only_the_last_log_lines_are_kept(tmp_path: Path) -> None:
    """Long logs are cut to their last lines, so saved runs stay small."""
    logs = "".join(f"line {number}\n" for number in range(LOG_LINES_KEPT + 50))
    folder = tmp_path / "run"
    run_fault(
        CATALOGUE["cable_loss"],
        1.0,
        folder,
        bench=FakeBench(logs),
        suite=FakeSuite(),
        default_version="0.1.0",
    )
    kept = (folder / "logs.txt").read_text().splitlines()
    assert (len(kept), kept[-1]) == (LOG_LINES_KEPT, f"line {LOG_LINES_KEPT + 49}")


def test_the_data_set_has_history_and_question_runs_and_replaces_older_ones(tmp_path: Path) -> None:
    """Three history runs and one question run per fault; a stale run from before is gone."""
    stale = tmp_path / "history" / "old-run"
    stale.mkdir(parents=True)
    saved = build_dataset(tmp_path, FakeBench(), FakeSuite(), DEFAULT_VERSION)
    names = sorted(str(path.relative_to(tmp_path)) for path in saved)
    assert names == sorted(
        [f"history/{name}-{number}" for name in CATALOGUE for number in (1, 2, 3)]
        + [f"questions/{name}" for name in CATALOGUE]
    )
    assert not stale.exists()


def test_main_runs_one_fault_and_says_where_it_went(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command line saves one run under --out and prints its totals."""
    code = main(["cable_loss", "--size", "1.5", "--out", str(tmp_path)], FakeBench(), FakeSuite())
    assert code == 0
    assert "2 failed, 0 errors, 5 passed" in capsys.readouterr().out
    (folder,) = tmp_path.iterdir()
    assert folder.name.startswith("cable_loss-")


def test_main_builds_the_data_set(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--dataset builds every run and says how many."""
    assert main(["--dataset", str(tmp_path)], FakeBench(), FakeSuite()) == 0
    assert f"Saved {4 * len(CATALOGUE)} runs" in capsys.readouterr().out


def test_main_reports_a_bench_problem_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bench that can't be controlled exits 1 with the reason, not a traceback."""
    suite = FakeSuite(error=BenchControlError("start the bench with make bench-up"))
    assert main(["cable_loss", "--out", str(tmp_path)], FakeBench(), suite) == 1
    assert capsys.readouterr().out == "start the bench with make bench-up\n"


@pytest.mark.parametrize("arguments", [[], ["cable_loss", "--dataset", "data"]])
def test_main_needs_exactly_one_of_a_fault_or_the_data_set(arguments: list[str]) -> None:
    """Neither or both is a usage error."""
    with pytest.raises(SystemExit):
        main(arguments, FakeBench(), FakeSuite())


def test_runs_as_a_module() -> None:
    """Python -m tools.fault_run works as make fault calls it; no arguments is a usage error."""
    completed = subprocess.run(
        [sys.executable, "-m", "tools.fault_run"], capture_output=True, text=True, check=False
    )
    assert completed.returncode == USAGE_ERROR_EXIT_CODE
    assert "give a fault name or --dataset" in completed.stderr


def test_a_run_whose_collection_failed_is_kept_and_reported(tmp_path: Path) -> None:
    """A suite that couldn't collect isn't 'nothing failed': the evidence is kept and reported."""
    folder = tmp_path / "broken"
    suite = FakeSuite(
        PASSING_TOTALS,
        exit_status=2,
        collection_errors=[{"id": "tests/bench/test_bench.py", "message": "ImportError"}],
    )
    with pytest.raises(UnusableRunError, match="couldn't collect"):
        run_fault(
            CATALOGUE["cable_loss"],
            1.0,
            folder,
            bench=FakeBench(),
            suite=suite,
            default_version=DEFAULT_VERSION,
        )
    assert (folder / "results.json").exists()


def test_a_run_pytest_could_not_finish_is_kept_and_reported(tmp_path: Path) -> None:
    """An exit status that means neither 'all passed' nor 'tests failed' is reported too."""
    folder = tmp_path / "interrupted"
    with pytest.raises(UnusableRunError, match="exit status 3"):
        run_fault(
            CATALOGUE["cable_loss"],
            1.0,
            folder,
            bench=FakeBench(),
            suite=FakeSuite(PASSING_TOTALS, exit_status=3),
            default_version=DEFAULT_VERSION,
        )
    assert (folder / "results.json").exists()


def test_a_failure_part_way_leaves_the_previous_data_set_in_place(tmp_path: Path) -> None:
    """If a later run fails, the old data set is untouched and no partial one is left behind."""
    kept = tmp_path / "history" / "cable_loss-1" / "results.json"
    kept.parent.mkdir(parents=True)
    kept.write_text("previous run", encoding="utf-8")
    with pytest.raises(BenchControlError):
        build_dataset(tmp_path, FakeBench(), FakeSuite(fail_after=2), DEFAULT_VERSION)
    assert kept.read_text(encoding="utf-8") == "previous run"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["history"]
