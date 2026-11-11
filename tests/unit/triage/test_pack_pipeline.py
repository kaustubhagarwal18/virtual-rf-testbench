"""Tests that a pack from another domain flows through the whole pipeline, with nothing RF."""

import json
from pathlib import Path

import pytest

from tests.unit.tools.test_triage_demo import StubRetriever
from tests.unit.triage.test_pack import WITH_CHECKS, payments_case, write_pack
from triage.answer import MalformedAnswerError, verdict_schema
from triage.case import Case
from triage.failures import triage_like_the_api
from triage.llm import FakeProvider
from triage.pack import Pack, load_pack
from triage.prompt import Source
from triage.service import triage_run
from triage.vote import vote_run


def payments(tmp_path: Path) -> Pack:
    """Load the payments pack, with its facts and checks."""
    return load_pack(write_pack(tmp_path, WITH_CHECKS))


def failed_batch(tmp_path: Path) -> Case:
    """Return a failed payments batch, saved and loaded as a generic case.json."""
    return payments_case(tmp_path)


def reply(key: str, cited: list[str] | None = None) -> str:
    """Return a model reply choosing the action with this key."""
    return json.dumps(
        {"fix": key, "where": "the feed", "because": "short file", "cited": cited or []}
    )


def test_the_prompt_and_schema_come_from_the_pack(tmp_path: Path) -> None:
    """The model is told about payments, offered payments actions, and constrained to them."""
    provider = FakeProvider([reply("REQUEST_FEED")])
    verdict = triage_run(failed_batch(tmp_path), provider, pack=payments(tmp_path))
    system, _ = provider.prompts[0]
    assert system.startswith("You triage failed payment batches.")
    assert "RF" not in system
    assert "CABLE" not in system
    assert provider.schemas[0] == verdict_schema(pack=payments(tmp_path))
    assert verdict.fix.key == "REQUEST_FEED"
    assert verdict.category == "feed"


def test_an_action_from_another_pack_is_refused(tmp_path: Path) -> None:
    """An RF key is not one of the payments actions, so the reply can't be used."""
    with pytest.raises(MalformedAnswerError, match="REQUEST_FEED"):
        triage_run(failed_batch(tmp_path), FakeProvider([reply("CABLE")]), pack=payments(tmp_path))


def test_the_vote_counts_the_packs_actions(tmp_path: Path) -> None:
    """Past runs recorded with payments actions vote for a payments action."""
    sources = [Source(f"past-{number}", "x", action="REQUEST_FEED") for number in range(3)]
    verdict = vote_run(failed_batch(tmp_path), sources, pack=payments(tmp_path))
    assert verdict.fix.key == "REQUEST_FEED"


def test_triage_like_the_api_applies_the_packs_checks(tmp_path: Path) -> None:
    """With no mapping change in the logs, the payments check refuses FIX_MAPPING."""
    source = Source("past-1", "x", action="FIX_MAPPING", fix_applied="Fixed the mapping")
    verdict = triage_like_the_api(
        failed_batch(tmp_path),
        FakeProvider([reply("FIX_MAPPING", ["past-1"])]),
        StubRetriever(source),
        payments(tmp_path),
    )
    assert verdict.fix.key == "UNKNOWN"
    assert verdict.overruled == "no mapping change was deployed"
