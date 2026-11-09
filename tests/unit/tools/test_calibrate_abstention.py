"""Tests for setting the abstention threshold from the history runs."""

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from tools.calibrate_abstention import Neighbours, main, neighbours, threshold
from triage.embed import FakeEmbedder
from triage.llm import ModelUnavailableError


def history_run(folder: Path, fault: str, message: str) -> None:
    """Write a labelled history run whose failure carries a distinctive message."""
    run = write_run(folder, [{**FAILING_TEST, "message": message}])
    label = {"fault": fault, "cause": "cause", "fix": "fix", "action": "CABLE"}
    (run / "label.json").write_text(json.dumps(label), encoding="utf-8")


def small_history(folder: Path) -> Path:
    """Write two faults with two runs each, worded so each pair is closest to itself."""
    history_run(folder / "cable_loss-1", "cable_loss", "path loss cable datasheet high")
    history_run(folder / "cable_loss-2", "cable_loss", "path loss cable datasheet higher")
    history_run(folder / "slow-1", "slow_responses", "timeout no reply from port")
    history_run(folder / "slow-2", "slow_responses", "timeout no reply from the port")
    return folder


def test_each_run_is_closer_to_its_own_fault_than_to_another(tmp_path: Path) -> None:
    """With distinct wording per fault, the same-fault neighbour wins for every run."""
    found = neighbours(small_history(tmp_path / "history"), FakeEmbedder())
    assert [entry.fault for entry in found] == ["cable_loss"] * 2 + ["slow_responses"] * 2
    for entry in found:
        assert entry.same_fault is not None
        assert entry.same_fault > entry.other_fault


@pytest.mark.parametrize(
    ("same", "expected"),
    [([0.951, 0.987], 0.95), ([0.999], 0.99), ([0.95, None], 0.95), ([0.5001, 0.9], 0.5)],
)
def test_the_threshold_is_the_lowest_precedent_rounded_down(
    same: Sequence[float | None], expected: float
) -> None:
    """Rounding down keeps every measured precedent at or above the threshold."""
    found = [Neighbours("fault", value, 0.1) for value in same]
    chosen = threshold(found)
    assert chosen == pytest.approx(expected)
    assert all(value is None or value >= chosen for value in same)


def test_a_history_with_no_repeated_fault_has_no_threshold() -> None:
    """With one run per fault no precedent was measured, so no threshold is made up."""
    with pytest.raises(ValueError, match="no fault has two history runs"):
        threshold([Neighbours("a", None, 0.4), Neighbours("b", None, 0.4)])


def test_the_command_prints_both_ranges_and_the_threshold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The output names the threshold and how many no-precedent runs fall below it."""
    assert main(["--history", str(small_history(tmp_path / "history"))], FakeEmbedder()) == 0
    out = capsys.readouterr().out
    assert "closest same-fault run:" in out
    assert "closest other-fault run:" in out
    assert "threshold: " in out
    assert "of 4 runs fall below it" in out


class UnreachableEmbedder:
    """An embedder whose model can't be reached."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Fail as Ollama does when it isn't running.

        Args:
            texts: The texts, never embedded.

        Raises:
            ModelUnavailableError: Always.
        """
        raise ModelUnavailableError(
            f"Ollama isn't running; start it with 'ollama serve' ({len(texts)})"
        )


def test_an_unreachable_model_is_one_line_and_exit_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No traceback: the message says what is wrong."""
    history = small_history(tmp_path / "history")
    assert main(["--history", str(history)], UnreachableEmbedder()) == 1
    assert "ollama serve" in capsys.readouterr().out


def test_a_history_too_small_to_calibrate_is_exit_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One run per fault gives no threshold, and the command says so."""
    history_run(tmp_path / "history" / "a-1", "a", "path loss")
    history_run(tmp_path / "history" / "b-1", "b", "timeout")
    assert main(["--history", str(tmp_path / "history")], FakeEmbedder()) == 1
    assert "no fault has two history runs" in capsys.readouterr().out


def test_calibration_runs_as_a_module() -> None:
    """Python -m tools.calibrate_abstention works the way the docs call it."""
    completed = subprocess.run(
        [sys.executable, "-m", "tools.calibrate_abstention", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "abstention threshold" in completed.stdout
