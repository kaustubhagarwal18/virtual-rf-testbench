"""Tests for the rules-only answerer: check rules alone, no model, no history."""

from pathlib import Path

from triage.answer import Verdict
from triage.case import Case, Item
from triage.checks import DEFAULT_CHECKS, VerdictCheck
from triage.pack import DEFAULT_PACK, UNKNOWN
from triage.rules import WHERE, rules_run, rules_verdict


def test_one_survivor_wins(tmp_path: Path) -> None:
    """When every action but one is refused, the survivor is the answer."""

    class RefuseAll(VerdictCheck):
        """Refuse every action except CABLE."""

        def review(self, run: Case, verdict: Verdict) -> Verdict:
            """Overrule anything that isn't CABLE."""
            if verdict.fix.key == "CABLE":
                return verdict
            return Verdict(fix=UNKNOWN, where=verdict.where, because="refused")

    run = Case(tmp_path, items=(Item("t", "failed", "boom"),))
    result = rules_verdict(run, (RefuseAll(),))
    assert result.fix.key == "CABLE"
    assert WHERE in result.where


def test_multiple_survivors_give_unknown(tmp_path: Path) -> None:
    """When the checks leave more than one action, UNKNOWN names the survivors."""

    class RefuseNothing(VerdictCheck):
        """Let everything through."""

        def review(self, run: Case, verdict: Verdict) -> Verdict:
            """Pass."""
            return verdict

    run = Case(tmp_path, items=(Item("t", "failed", "boom"),))
    result = rules_verdict(run, (RefuseNothing(),))
    assert result.fix is UNKNOWN
    assert "not one" in result.because


def test_no_survivors_give_unknown(tmp_path: Path) -> None:
    """When every action is refused, UNKNOWN says so."""

    class RefuseEverything(VerdictCheck):
        """Refuse all actions."""

        def review(self, run: Case, verdict: Verdict) -> Verdict:
            """Overrule everything."""
            return Verdict(fix=UNKNOWN, where=verdict.where, because="refused")

    run = Case(tmp_path, items=(Item("t", "failed", "boom"),))
    result = rules_verdict(run, (RefuseEverything(),))
    assert result.fix is UNKNOWN
    assert "every action was refused" in result.because


def test_rules_run_passes_through_checks(tmp_path: Path) -> None:
    """The rules_run function applies the checks after the rules verdict, like vote_run."""
    run = Case(tmp_path, items=(Item("t", "failed", "boom"),))
    result = rules_run(run, checks=DEFAULT_CHECKS)
    assert result.fix.key in DEFAULT_PACK.keys
