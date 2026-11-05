"""Tests for the pipeline: evidence, prompt, answer."""

import json
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from triage.answer import MalformedAnswerError
from triage.evidence import ERROR_ONLY, FULL, SavedRun
from triage.llm import FakeProvider
from triage.prompt import Fix, Source
from triage.service import triage_run

VERDICT = json.dumps(
    {"fix": "CABLE", "where": "the bench cable", "because": "the loss is high", "cited": []}
)


def saved_run(tmp_path: Path) -> SavedRun:
    """Return a saved run with one failing measurement."""
    return SavedRun.load(write_run(tmp_path / "run", [FAILING_TEST]))


def test_the_chosen_action_comes_back_as_the_verdict(tmp_path: Path) -> None:
    """A well-formed reply becomes the verdict the caller acts on."""
    verdict = triage_run(saved_run(tmp_path), FakeProvider([VERDICT]))
    assert verdict.fix is Fix.CABLE
    assert verdict.where == "the bench cable"


def test_the_evidence_level_decides_what_the_model_is_told(tmp_path: Path) -> None:
    """The error-only scenario sees the message; the full one sees the firmware too."""
    run = saved_run(tmp_path)
    bare, full = FakeProvider([VERDICT]), FakeProvider([VERDICT])
    triage_run(run, bare, ERROR_ONLY)
    triage_run(run, full, FULL)
    assert "Firmware" not in bare.prompts[0][1]
    assert "Firmware" in full.prompts[0][1]


def test_sources_reach_the_prompt_and_may_be_cited(tmp_path: Path) -> None:
    """With history offered, the model sees it and a citation to it is accepted."""
    sources = [Source("cable-loss-03", "path loss 1.9 dB; cause: bench; fixed: new cable")]
    reply = json.dumps(
        {"fix": "CABLE", "where": "cable", "because": "same as before", "cited": ["cable-loss-03"]}
    )
    provider = FakeProvider([reply])
    verdict = triage_run(saved_run(tmp_path), provider, FULL, sources)
    assert "[cable-loss-03]" in provider.prompts[0][1]
    assert verdict.cited == ["cable-loss-03"]


def test_a_citation_that_was_never_offered_is_refused(tmp_path: Path) -> None:
    """Without sources, any citation is invented, and the run counts as unusable."""
    reply = json.dumps({"fix": "CABLE", "where": "cable", "because": "x", "cited": ["run-42"]})
    with pytest.raises(MalformedAnswerError, match="cites sources it wasn't given"):
        triage_run(saved_run(tmp_path), FakeProvider([reply]))


def test_a_malformed_reply_is_not_retried(tmp_path: Path) -> None:
    """How often the model answers unusably is a result, so the pipeline asks only once."""
    provider = FakeProvider(["sorry, I can't help with that"])
    with pytest.raises(MalformedAnswerError):
        triage_run(saved_run(tmp_path), provider)
    assert len(provider.prompts) == 1
