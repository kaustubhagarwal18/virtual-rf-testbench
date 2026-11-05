"""Tests for retrieving the past runs most like a new one."""

import json
import tempfile
from pathlib import Path

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from triage.embed import FakeEmbedder
from triage.evidence import SavedRun
from triage.retrieval import InMemoryRetriever, history_source


def labelled_run(folder: Path, fault: str, cause: str, fix: str) -> Path:
    """Write a history run with a label."""
    run = write_run(folder, [FAILING_TEST])
    (run / "label.json").write_text(
        json.dumps({"fault": fault, "cause": cause, "fix": fix}), encoding="utf-8"
    )
    return run


def test_a_history_source_carries_its_cause_and_fix() -> None:
    """A past run is only useful if it says how it was resolved, so the source includes both."""
    with tempfile.TemporaryDirectory() as folder:
        run = SavedRun.load(
            labelled_run(Path(folder) / "cable_loss-1", "cable_loss", "worn cable", "replaced it")
        )
        source = history_source(run)
        assert source.source_id == "cable_loss-1"
        assert "worn cable" in source.text
        assert "replaced it" in source.text


def run_with_error(folder: Path, fault: str, message: str) -> Path:
    """Write a history run whose single failure carries a distinctive message."""
    test = {**FAILING_TEST, "message": message}
    run = write_run(folder, [test])
    (run / "label.json").write_text(
        json.dumps({"fault": fault, "cause": fault, "fix": fault}), encoding="utf-8"
    )
    return run


def test_the_nearest_run_shares_the_most_words(tmp_path: Path) -> None:
    """A query retrieves the history run whose evidence (not its hidden label) overlaps it most.

    Different faults leave different evidence, so the match is driven by what the model would
    actually see, not by the label, which the query never includes.
    """
    history = tmp_path / "history"
    run_with_error(history / "cable_loss-1", "cable_loss", "path loss cable datasheet exceeded")
    run_with_error(history / "slow_responses-1", "slow_responses", "timeout no reply from port")
    retriever = InMemoryRetriever.from_history(history, FakeEmbedder())
    question = SavedRun.load(
        run_with_error(tmp_path / "q" / "cable_loss", "cable_loss", "path loss cable datasheet off")
    )
    top = retriever.similar_to(question, count=1)
    assert top[0].source_id == "cable_loss-1"


def test_count_limits_how_many_are_returned(tmp_path: Path) -> None:
    """Retrieval returns at most the number asked for."""
    history = tmp_path / "history"
    for index in range(4):
        labelled_run(history / f"cable_loss-{index}", "cable_loss", "cable", "new cable")
    retriever = InMemoryRetriever.from_history(history, FakeEmbedder())
    question = SavedRun.load(labelled_run(tmp_path / "q" / "cable_loss", "cable_loss", "c", "f"))
    wanted = 2
    assert len(retriever.similar_to(question, count=wanted)) == wanted
