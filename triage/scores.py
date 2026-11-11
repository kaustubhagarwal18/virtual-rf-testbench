"""What one evaluation run measured: the scenarios' definitions, outcomes and scores.

The data the evaluation produces and the report reads, kept apart from both: triage.evaluate
fills these in by asking questions, and triage.report writes them up.
"""

import math
from dataclasses import dataclass, field
from enum import StrEnum

from triage.prompt import Fix

MALFORMED = "malformed"
"""What a reply that couldn't be parsed counts as: neither right nor a real wrong answer."""

Z_95 = 1.96
"""The normal quantile for a two-sided 95% interval."""


class Precedents(StrEnum):
    """Which past runs a scenario offers the model."""

    NONE = "none"
    """No retrieval: the model has the run's evidence alone."""
    ALL = "all"
    """The past runs most like this one, of any fault."""
    OWN_FAULT_HELD_OUT = "own fault held out"
    """The same, with the question's own fault kept out, so the scenario measures what the
    model does with no precedent of that fault. Another fault with the same fix (link_down
    for link_flaky) may still be offered."""


@dataclass(frozen=True)
class Scenario:
    """One way of asking the model, so scenarios can be compared.

    Attributes:
        name: What the scenario is called in the report.
        level: The evidence level the model is given.
        precedents: Which past runs are offered as sources: none, or the closest ones with
            or without the question's own fault.
        checked: Whether the verdict goes through the service's checks.
        gated: Whether the precedents go through the abstention gates before the model is
            asked, so a question with no basis for an answer gets UNKNOWN unasked.
        vote: Whether the precedents' majority vote answers instead of the model, which is
            then never asked; needs precedents to vote.
        adds_to: The scenario this one changes exactly one thing in; empty for the first.
    """

    name: str
    level: str
    precedents: Precedents = Precedents.NONE
    checked: bool = False
    gated: bool = False
    vote: bool = False
    adds_to: str = ""

    @property
    def retrieve(self) -> bool:
        """Whether the past runs most like this one are offered as sources.

        Returns:
            True unless the scenario offers no precedents.
        """
        return self.precedents is not Precedents.NONE

    @property
    def hold_out(self) -> bool:
        """Whether past runs of the question's own fault are kept out of retrieval.

        Returns:
            True when the scenario holds the question's own fault out.
        """
        return self.precedents is Precedents.OWN_FAULT_HELD_OUT


@dataclass(frozen=True)
class SourcesUsed:
    """The past runs a question was offered, and which of them the model cited.

    Attributes:
        offered_faults: The labelled fault of each source offered, most similar first;
            empty when the scenario doesn't retrieve.
        cited: The source ids the model cited; empty when it cited none or was unusable.
        cited_faults: The labelled fault of each cited source, in the same order.
        quoted_faults: The labelled fault of each past run whose recorded fix the answer
            quotes, cited or not, in the order quoted.
        gated: Whether a gate found the offered sources no basis for an answer, so UNKNOWN
            was given and the model wasn't asked.
    """

    offered_faults: tuple[str, ...] = ()
    cited: tuple[str, ...] = ()
    cited_faults: tuple[str, ...] = ()
    quoted_faults: tuple[str, ...] = ()
    gated: bool = False


@dataclass(frozen=True)
class Outcome:
    """One question asked once, in one scenario.

    Attributes:
        question: The question run's folder name.
        fault: The fault the run holds, from its label, which names the expected fix.
        expected: The fix that resolves it.
        chosen: The fix the model chose, or MALFORMED when the reply couldn't be used.
        error: The failure the model was shown, as one line.
        answer: What the model said: its chosen fix, where and why, or the unusable reply.
        sources: The past runs offered, which the model cited, and whether a gate answered.
    """

    question: str
    fault: str
    expected: Fix
    chosen: str
    error: str = ""
    answer: str = ""
    sources: SourcesUsed = SourcesUsed()

    @property
    def right(self) -> bool:
        """Whether the chosen fix is the one that resolves the fault.

        Returns:
            True for the resolving fix.
        """
        return self.chosen == self.expected.name

    @property
    def abstained(self) -> bool:
        """Whether the model said UNKNOWN rather than act.

        Returns:
            True for UNKNOWN, unless UNKNOWN were itself the expected fix.
        """
        return not self.right and self.chosen == Fix.UNKNOWN.name

    @property
    def confidently_wrong(self) -> bool:
        """Whether the model named an action, and it was the wrong one.

        Returns:
            True for a wrong action that isn't UNKNOWN or an unusable reply.
        """
        return not self.right and not self.abstained and self.chosen != MALFORMED

    @property
    def precedent_offered(self) -> bool:
        """Whether retrieval offered at least one past run of the question's own fault.

        Returns:
            True if one of the offered sources has the question's fault.
        """
        return self.fault in self.sources.offered_faults

    @property
    def cites_own_fault(self) -> bool:
        """Whether every past run it cited has the question's own fault.

        Only then is the "last time" fix it quotes one that resolved this fault.

        Returns:
            True when it cited at least one run and all of them are of its fault.
        """
        cited_faults = self.sources.cited_faults
        return bool(cited_faults) and all(fault == self.fault for fault in cited_faults)

    @property
    def quotes_own_fault(self) -> bool:
        """Whether one of the fixes the answer quotes is from a past run of its own fault.

        Returns:
            True when a quoted fix is the fault's own, so the "last time" a reader sees
            includes what actually resolved this fault.
        """
        return self.fault in self.sources.quoted_faults


@dataclass
class ScenarioResult:
    """Every outcome for one scenario.

    Attributes:
        scenario: The scenario that was scored.
        outcomes: One outcome per question asked.
    """

    scenario: Scenario
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def name(self) -> str:
        """The scenario's name, for the report.

        Returns:
            The name.
        """
        return self.scenario.name

    @property
    def total(self) -> int:
        """How many questions were asked.

        Returns:
            The count.
        """
        return len(self.outcomes)

    @property
    def right(self) -> int:
        """How many questions got the resolving fix.

        Returns:
            The count.
        """
        return sum(1 for outcome in self.outcomes if outcome.right)

    @property
    def abstained(self) -> int:
        """How many questions the model answered UNKNOWN.

        Returns:
            The count.
        """
        return sum(1 for outcome in self.outcomes if outcome.abstained)

    @property
    def malformed(self) -> int:
        """How many replies couldn't be used.

        Returns:
            The count.
        """
        return sum(1 for outcome in self.outcomes if outcome.chosen == MALFORMED)

    @property
    def confidently_wrong(self) -> int:
        """How many questions got a wrong action that wasn't UNKNOWN.

        Returns:
            The count.
        """
        return sum(1 for outcome in self.outcomes if outcome.confidently_wrong)

    @property
    def precedent_offered(self) -> int:
        """How many questions retrieval offered a past run of their own fault for.

        Returns:
            The count; zero for a scenario that doesn't retrieve.
        """
        return sum(1 for outcome in self.outcomes if outcome.precedent_offered)

    @property
    def right_citing_own_fault(self) -> int:
        """How many right answers cited only past runs of their own fault.

        Returns:
            The count.
        """
        return sum(1 for outcome in self.outcomes if outcome.right and outcome.cites_own_fault)

    @property
    def right_citing_another_fault(self) -> int:
        """How many right answers cited a past run of another fault, so quote its fix.

        Returns:
            The count.
        """
        return sum(
            1
            for outcome in self.outcomes
            if outcome.right and outcome.sources.cited_faults and not outcome.cites_own_fault
        )

    @property
    def right_citing_nothing(self) -> int:
        """How many right answers cited no past run.

        Returns:
            The count.
        """
        return sum(1 for outcome in self.outcomes if outcome.right and not outcome.sources.cited)

    @property
    def right_quoting_own_fault(self) -> int:
        """How many right answers quote at least one fix of their own fault.

        Returns:
            The count.
        """
        return sum(1 for outcome in self.outcomes if outcome.right and outcome.quotes_own_fault)

    @property
    def by_fault(self) -> dict[str, tuple[int, int]]:
        """Count each fault's questions: how many were right, of how many asked.

        Returns:
            (right, asked) for each fault, in name order.
        """
        counts: dict[str, tuple[int, int]] = {}
        for outcome in self.outcomes:
            right, asked = counts.get(outcome.fault, (0, 0))
            counts[outcome.fault] = (right + outcome.right, asked + 1)
        return dict(sorted(counts.items()))

    @property
    def faults(self) -> int:
        """How many faults were asked about.

        Returns:
            The count.
        """
        return len(self.by_fault)

    @property
    def faults_solved(self) -> int:
        """How many faults had every one of their questions right.

        Returns:
            The count.
        """
        return sum(1 for right, asked in self.by_fault.values() if right == asked)

    @property
    def faults_split(self) -> int:
        """How many faults had some questions right and some wrong.

        Returns:
            The count; zero means a fault's questions always behave as one.
        """
        return sum(1 for right, asked in self.by_fault.values() if 0 < right < asked)

    @property
    def wrong_choices(self) -> dict[str, int]:
        """What the model chose when it named a wrong action, counted.

        Returns:
            Each confident wrong choice and how often it was made, most frequent first.
        """
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            if outcome.confidently_wrong:
                counts[outcome.chosen] = counts.get(outcome.chosen, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: -item[1]))


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    """Return the 95% Wilson score interval for a count, as counts rather than a fraction.

    The Wilson interval stays inside 0..total and behaves at small counts and at the
    extremes, where the textbook normal interval does not.

    Args:
        successes: How many were right.
        total: How many were asked.

    Returns:
        The interval's low and high ends, in the unit counted; (0, 0) when nothing was asked.
    """
    if total == 0:
        return 0.0, 0.0
    proportion = successes / total
    spread = Z_95**2 / total
    centre = (proportion + spread / 2) / (1 + spread)
    half_width = (
        Z_95
        * math.sqrt(proportion * (1 - proportion) / total + Z_95**2 / (4 * total**2))
        / (1 + spread)
    )
    # At none or all right, that end is exactly 0 or total; floating point misses by 1e-16.
    low = 0.0 if successes == 0 else (centre - half_width) * total
    high = float(total) if successes == total else (centre + half_width) * total
    return low, high
