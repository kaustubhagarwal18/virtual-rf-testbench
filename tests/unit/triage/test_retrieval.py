"""Tests for retrieving the past runs most like a new one."""

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from triage.bench_run import load_case
from triage.embed import FakeEmbedder, OllamaEmbedder
from triage.label import load_label
from triage.retrieval import InMemoryRetriever, history_index, history_source, neutral_id


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
        run = load_case(
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
    run = load_case(labelled_run(tmp_path / folder_name, "cable_loss", "worn", "replaced"))
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
    question = load_case(
        run_with_error(tmp_path / "q" / "cable_loss", "cable_loss", "path loss cable datasheet off")
    )
    top = retriever.similar_to(question, count=1)
    assert top[0].fault == "cable_loss"


def test_count_limits_how_many_are_returned(tmp_path: Path) -> None:
    """Retrieval returns at most the number asked for."""
    history = tmp_path / "history"
    for index in range(4):
        labelled_run(history / f"cable_loss-{index}", "cable_loss", f"cable {index}", "new cable")
    retriever = InMemoryRetriever.from_history(history, FakeEmbedder())
    question = load_case(labelled_run(tmp_path / "q" / "cable_loss", "cable_loss", "c", "f"))
    wanted = 2
    assert len(retriever.similar_to(question, count=wanted)) == wanted


def test_a_held_out_fault_is_never_returned(tmp_path: Path) -> None:
    """Excluding a fault removes every run of it, even the closest, and keeps the rest."""
    history = tmp_path / "history"
    run_with_error(history / "cable_loss-1", "cable_loss", "path loss cable datasheet exceeded")
    run_with_error(history / "cable_loss-2", "cable_loss", "path loss cable datasheet high")
    run_with_error(history / "slow_responses-1", "slow_responses", "timeout no reply from port")
    retriever = InMemoryRetriever.from_history(history, FakeEmbedder())
    question = load_case(
        run_with_error(tmp_path / "q" / "cable_loss", "cable_loss", "path loss cable datasheet")
    )
    kept = retriever.similar_to(question, count=3, exclude_fault="cable_loss")
    assert [source.fault for source in kept] == ["slow_responses"]


def test_an_added_run_can_be_retrieved_and_is_kept_once(tmp_path: Path) -> None:
    """A resolved run added later is searched like the rest; adding it again changes nothing."""
    retriever = InMemoryRetriever([], np.zeros((0, 0)), FakeEmbedder())
    question = load_case(run_with_error(tmp_path / "q", "cable_loss", "path loss cable"))
    assert not retriever.similar_to(question, count=3)
    resolved = load_case(run_with_error(tmp_path / "new", "cable_loss", "path loss cable"))
    label = {"fault": "cable_loss", "cause": "worn cable", "fix": "replaced it"}
    first = retriever.add(resolved, label)
    second = retriever.add(resolved, label)
    assert first == second
    assert len(retriever.sources) == 1
    found = retriever.similar_to(question, count=3)
    assert [source.source_id for source in found] == [first.source_id]
    assert found[0].similarity is not None


@pytest.mark.parametrize(("action", "shown"), [("LINK", True), (None, False), ("", False)])
def test_a_source_states_the_action_taken_when_the_label_records_one(
    tmp_path: Path, action: str | None, shown: bool
) -> None:
    """A precedent hands the model the action key itself, when the ticket recorded it."""
    run = load_case(labelled_run(tmp_path / "run", "link_down", "unplugged", "reconnected"))
    label = {"fault": "link_down", "cause": "unplugged", "fix": "reconnected"}
    if action is not None:
        label["action"] = action
    text = history_source(run, label).text
    assert ("Action: LINK" in text) is shown
    assert ("Action:" in text) is shown


def test_retrieved_sources_carry_their_action_and_similarity(tmp_path: Path) -> None:
    """A source says how its run was resolved and how close it came, most similar first."""
    history = tmp_path / "history"
    for name, message, action in [
        ("a", "path loss cable datasheet exceeded", "CABLE"),
        ("b", "timeout no reply from port", "TIMEOUT"),
    ]:
        run = run_with_error(history / name, name, message)
        label = json.loads((run / "label.json").read_text(encoding="utf-8"))
        (run / "label.json").write_text(json.dumps({**label, "action": action}), "utf-8")
    retriever = InMemoryRetriever.from_history(history, FakeEmbedder())
    question = load_case(run_with_error(tmp_path / "q", "a", "path loss cable datasheet"))
    found = retriever.similar_to(question, count=2)
    assert [source.action for source in found] == ["CABLE", "TIMEOUT"]
    similarities = [source.similarity for source in found if source.similarity is not None]
    assert len(similarities) == len(found)
    assert similarities == sorted(similarities, reverse=True)
    assert all(source.similarity is None for source in retriever.sources)


@pytest.mark.parametrize("copies", [2, 3, 5])
def test_identical_history_runs_are_indexed_once(tmp_path: Path, copies: int) -> None:
    """Copies of one run take one place, so the top k stays k distinct runs."""
    history = tmp_path / "history"
    for index in range(copies):
        labelled_run(history / f"copy-{index}", "cable_loss", "worn cable", "replaced it")
    labelled_run(history / "other", "cable_loss", "kinked cable", "replaced it")
    retriever = InMemoryRetriever.from_history(history, FakeEmbedder())
    distinct = 2
    assert len(retriever) == distinct
    question = load_case(labelled_run(tmp_path / "q" / "q", "cable_loss", "c", "f"))
    found = retriever.similar_to(question, count=distinct)
    assert len({source.source_id for source in found}) == distinct


def test_a_history_source_keeps_the_fix_applied_apart(tmp_path: Path) -> None:
    """The fix a ticket records is kept on its own, so an answer can quote it word for word."""
    run = load_case(labelled_run(tmp_path / "run", "cable_loss", "worn cable", "replaced it"))
    source = history_source(run, load_label(run))
    assert source.fix_applied == "replaced it"


def test_history_index_uses_the_embedder_given(tmp_path: Path) -> None:
    """With an embedder injected, the history is indexed without reaching Ollama."""
    labelled_run(tmp_path / "history" / "cable_loss-1", "cable_loss", "worn cable", "new cable")
    retriever = history_index(tmp_path / "history", FakeEmbedder())
    assert len(retriever.sources) == 1


def test_history_index_defaults_to_the_ollama_embedder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without an embedder, the local Ollama one is used; nothing is embedded in this test."""
    used: list[object] = []
    empty = InMemoryRetriever([], np.zeros((0, 0)), FakeEmbedder())

    def record(history: Path, embedder: object) -> InMemoryRetriever:
        """Record the embedder instead of indexing."""
        del history
        used.append(embedder)
        return empty

    monkeypatch.setattr(InMemoryRetriever, "from_history", record)
    assert history_index(tmp_path) is empty
    assert isinstance(used[0], OllamaEmbedder)
