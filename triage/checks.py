"""Checks that turn an answer the evidence contradicts into UNKNOWN.

A small model has fallback answers: when unsure, it blames the firmware or the timeout, or
copies a precedent's diagnosis whole. But some facts are not judgements, they are recorded:
which versions the run ran, whether that changed, which errors were reported. A check turns
an answer that contradicts such a fact into UNKNOWN. It never picks a different action in
its place: that would be a guess, and a confident wrong action is the failure that costs
an engineer hours.

Which actions to refuse, on which facts, is domain knowledge, so it lives in the domain
pack as rules (`[[checks]]` in `triage/packs/rf-bench.toml`); this module applies them. A
check too subtle for a rule can still be a class with a review method.
"""

from dataclasses import dataclass, replace
from typing import Protocol

from triage.answer import Verdict
from triage.case import Case
from triage.facts import Fact
from triage.pack import DEFAULT_PACK, UNKNOWN, CheckRule, Pack


class VerdictCheck(Protocol):
    """Anything that can review a verdict against the run it is about."""

    def review(self, run: Case, verdict: Verdict) -> Verdict:
        """Return the verdict, or UNKNOWN in its place if it contradicts the evidence.

        Args:
            run: The run the verdict is about.
            verdict: The model's verdict.

        Returns:
            The verdict unchanged, or an UNKNOWN verdict that says why it was overruled.
        """


@dataclass(frozen=True)
class RuleCheck:
    """A pack's check rule, with the fact it reads.

    Attributes:
        rule: The rule: which action it refuses, on which fact, and why.
        fact: The fact the rule names.
    """

    rule: CheckRule
    fact: Fact

    def review(self, run: Case, verdict: Verdict) -> Verdict:
        """Overrule the rule's action when its fact holds, or unless it does.

        Args:
            run: The run the verdict is about, whole or focused on one failure.
            verdict: The model's verdict.

        Returns:
            The verdict, or UNKNOWN with the rule's reason, quoting the fact's values.
        """
        if verdict.fix.key != self.rule.refuse:
            return verdict
        values = self.fact(run, f"{verdict.where} {verdict.because}")
        if (values is not None) != self.rule.when:
            return verdict
        return replace(verdict, fix=UNKNOWN, overruled=self.rule.reason.format(**(values or {})))


def checks_for(pack: Pack) -> tuple[VerdictCheck, ...]:
    """Build a pack's checks, in the order its file lists them.

    Args:
        pack: The domain pack.

    Returns:
        One check per rule.
    """
    return tuple(RuleCheck(rule, pack.facts[rule.fact]) for rule in pack.checks)


DEFAULT_CHECKS: tuple[VerdictCheck, ...] = checks_for(DEFAULT_PACK)
"""The default pack's checks, which the service applies to every verdict."""


def apply_checks(run: Case, verdict: Verdict, checks: tuple[VerdictCheck, ...]) -> Verdict:
    """Pass a verdict through each check in turn.

    Args:
        run: The run the verdict is about.
        verdict: The model's verdict.
        checks: The checks, applied in order.

    Returns:
        The verdict after every check has reviewed it.
    """
    for check in checks:
        verdict = check.review(run, verdict)
    return verdict
