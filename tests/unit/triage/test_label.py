"""Tests for reading a saved run's label."""

import json
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from triage.bench_run import load_case
from triage.label import load_label


def test_the_label_is_read_from_beside_the_evidence(tmp_path: Path) -> None:
    """The label's fields come back as written."""
    folder = write_run(tmp_path / "cable_loss-1", [FAILING_TEST])
    written = {"fault": "cable_loss", "cause": "worn cable", "fix": "replaced it"}
    (folder / "label.json").write_text(json.dumps(written), encoding="utf-8")
    assert load_label(load_case(folder)) == written


def test_a_missing_label_is_reported(tmp_path: Path) -> None:
    """A run without a label raises, so a caller can say what's missing."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    (folder / "label.json").unlink()
    with pytest.raises(FileNotFoundError):
        load_label(load_case(folder))
