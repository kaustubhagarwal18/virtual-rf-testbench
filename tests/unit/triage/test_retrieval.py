"""Tests for retrieving the past runs most like a new one."""

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from triage.embed import FakeEmbedder
from triage.evidence import SavedRun
from triage.label import load_label
from triage.retrieval import InMemoryRetriever, history_source, neutral_id


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
        source = history_source(run, load_label(run))
        assert "worn cable" in source.text
        assert "replaced it" in source.text
        assert source.fault == "cable_loss"


@pytest.mark.parametrize(
    "folder_name", ["cable_loss-1", "slow_responses-3", "output_command_change-2", "run"]
)
def test_a_source_id_never_names_the_run_or_its_fault(tmp_path: Path, folder_name: str) -> None:
    """The id the model cites is a hash, so it can't read the cause off a folder name."""
    run = SavedRun.load(labelled_run(tmp_path / folder_name, "cable_loss", "worn", "replaced"))
    source = history_source(run, load_label(run))
    assert source.source_id == neutral_id(source.text)
    assert source.source_id.startswith("past-")
    for word in (folder_name, "cable", "loss", "slow", "output"):
        assert word not in source.source_id


def test_the_same_text_always_gets_the_same_id() -> None:
    """A neutral id is stable, so a report and a rerun name a source the same way."""
    assert neutral_id("path loss high") == neutral_id("path loss high")
    assert neutral_id("path loss high") != neutral_id("path loss low")


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
    assert top[0].fault == "cable_loss"


def test_count_limits_how_many_are_returned(tmp_path: Path) -> None:
    """Retrieval returns at most the number asked for."""
    history = tmp_path / "history"
    for index in range(4):
        labelled_run(history / f"cable_loss-{index}", "cable_loss", "cable", "new cable")
    retriever = InMemoryRetriever.from_history(history, FakeEmbedder())
    question = SavedRun.load(labelled_run(tmp_path / "q" / "cable_loss", "cable_loss", "c", "f"))
    wanted = 2
    assert len(retriever.similar_to(question, count=wanted)) == wanted


def test_a_held_out_fault_is_never_returned(tmp_path: Path) -> None:
    """Excluding a fault removes every run of it, even the closest, and keeps the rest."""
    history = tmp_path / "history"
    run_with_error(history / "cable_loss-1", "cable_loss", "path loss cable datasheet exceeded")
    run_with_error(history / "cable_loss-2", "cable_loss", "path loss cable datasheet high")
    run_with_error(history / "slow_responses-1", "slow_responses", "timeout no reply from port")
    retriever = InMemoryRetriever.from_history(history, FakeEmbedder())
    question = SavedRun.load(
        run_with_error(tmp_path / "q" / "cable_loss", "cable_loss", "path loss cable datasheet")
    )
    kept = retriever.similar_to(question, count=3, exclude_fault="cable_loss")
    assert [source.fault for source in kept] == ["slow_responses"]


def test_an_added_run_can_be_retrieved_and_is_kept_once(tmp_path: Path) -> None:
    """A resolved run added later is searched like the rest; adding it again changes nothing."""
    retriever = InMemoryRetriever([], np.zeros((0, 0)), FakeEmbedder())
    question = SavedRun.load(run_with_error(tmp_path / "q", "cable_loss", "path loss cable"))
    assert not retriever.similar_to(question, count=3)
    resolved = SavedRun.load(run_with_error(tmp_path / "new", "cable_loss", "path loss cable"))
    label = {"fault": "cable_loss", "cause": "worn cable", "fix": "replaced it"}
    first = retriever.add(resolved, label)
    second = retriever.add(resolved, label)
    assert first == second
    assert len(retriever.sources) == 1
    assert retriever.similar_to(question, count=3) == [first]
