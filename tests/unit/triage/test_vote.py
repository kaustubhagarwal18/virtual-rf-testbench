"""Tests for the majority vote: the precedents' most common action, with no model asked."""

import itertools
import json
from collections import Counter
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from triage.abstain import PrecedentsDisagree
from triage.checks import DEFAULT_CHECKS
from triage.evidence import SavedRun
from triage.pack import DEFAULT_PACK, UNKNOWN
from triage.prompt import Source
from triage.vote import majority_vote, vote_run


def precedent(action: str, index: int = 0) -> Source:
    """Return a past run resolved by an action, under an id that tells precedents apart."""
    return Source(f"past-{index}-{action}", "text", "fault", action, None)


ACTIONS = ["", "CABLE", "LINK", "FIRMWARE"]
"""What a precedent's label may record: nothing, or one of a few keys."""


def precedents(actions: tuple[str, ...]) -> list[Source]:
    """Return one precedent per action, most similar first."""
    return [precedent(action, index) for index, action in enumerate(actions)]


@pytest.mark.parametrize("count", [1, 2, 3])
def test_every_mix_of_precedents_gets_the_strictly_most_common_action(count: int) -> None:
    """Every mix of one to three precedents: the vote is the one action with the most votes."""
    for actions in itertools.product(ACTIONS, repeat=count):
        votes = Counter(action for action in actions if action)
        ranked = votes.most_common()
        expected = (
            DEFAULT_PACK.action(ranked[0][0])
            if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1])
            else UNKNOWN
        )
        assert majority_vote(precedents(actions)).fix is expected, actions


def test_no_precedents_is_unknown() -> None:
    """With nothing retrieved there is nothing to vote on, so the answer is UNKNOWN."""
    result = majority_vote([])
    assert result.fix is UNKNOWN
    assert not result.cited


def test_the_vote_cites_the_precedents_that_voted_for_it() -> None:
    """The cited ids are the winning precedents', in similarity order, and no others."""
    result = majority_vote(precedents(("CABLE", "LINK", "CABLE")))
    assert result.fix is DEFAULT_PACK.action("CABLE")
    assert result.cited == ["past-0-CABLE", "past-2-CABLE"]
    assert "2 of the 3 closest past runs" in result.because


def test_a_tie_says_which_actions_tied() -> None:
    """A three-way split is UNKNOWN, and the reason names every action in it."""
    result = majority_vote(precedents(("CABLE", "LINK", "FIRMWARE")))
    assert result.fix is UNKNOWN
    for action in ("CABLE", "LINK", "FIRMWARE"):
        assert action in result.because


def test_precedents_without_a_recorded_action_say_so() -> None:
    """Precedents whose action wasn't recorded give nothing to vote on, and the reason says so."""
    result = majority_vote(precedents(("", "")))
    assert result.fix is UNKNOWN
    assert "recorded" in result.because


def test_an_action_that_is_not_on_the_list_is_an_error() -> None:
    """A label recording an action the list doesn't have is a broken data set, not a vote."""
    with pytest.raises(KeyError, match="make fault-dataset"):
        majority_vote(precedents(("REPLACE_EVERYTHING",)))


def unchanged_firmware_run(folder: Path) -> SavedRun:
    """Write a failed run whose firmware is the same as the last passing run's."""
    run = write_run(folder, [FAILING_TEST])
    results = json.loads((run / "results.json").read_text(encoding="utf-8"))
    results["firmware_versions"] = {"analyzer": "0.1.0", "generator": "0.1.0"}
    (run / "results.json").write_text(json.dumps(results), encoding="utf-8")
    return SavedRun.load(run)


def test_the_checks_overrule_a_vote_as_they_would_the_model(tmp_path: Path) -> None:
    """A FIRMWARE vote on unchanged firmware becomes UNKNOWN, with the check's reason."""
    run = unchanged_firmware_run(tmp_path / "run")
    sources = precedents(("FIRMWARE", "FIRMWARE"))
    assert vote_run(run, sources).fix is DEFAULT_PACK.action("FIRMWARE")
    checked = vote_run(run, sources, checks=DEFAULT_CHECKS)
    assert checked.fix is UNKNOWN
    assert checked.overruled


def test_a_gate_answers_before_the_vote(tmp_path: Path) -> None:
    """With the disagreement gate, a 2-to-1 vote is UNKNOWN and records the gate's reason."""
    run = unchanged_firmware_run(tmp_path / "run")
    sources = precedents(("CABLE", "LINK", "CABLE"))
    assert vote_run(run, sources).fix is DEFAULT_PACK.action("CABLE")
    gated = vote_run(run, sources, gates=(PrecedentsDisagree(),))
    assert gated.fix is UNKNOWN
    assert gated.gate_reason


def test_a_vote_quotes_the_fixes_of_the_runs_that_voted_for_it(tmp_path: Path) -> None:
    """The winning precedents' recorded fixes come back with the vote, the losers' do not."""
    run = unchanged_firmware_run(tmp_path / "run")
    sources = [
        Source("past-a", "a", "fault", "CABLE", None, fix_applied="replaced the cable"),
        Source("past-b", "b", "fault", "LINK", None, fix_applied="reseated the link"),
        Source("past-c", "c", "fault", "CABLE", None, fix_applied="new cable, recalibrated"),
    ]
    fixes = [past.fix_applied for past in vote_run(run, sources).past_fixes]
    assert fixes == ["replaced the cable", "new cable, recalibrated"]
