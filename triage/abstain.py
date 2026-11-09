"""Abstaining from retrieval alone: answering UNKNOWN before the model is asked.

The checks in triage.checks read the model's verdict against the run. These gates read the
precedents retrieval found, before there is a verdict. With the question's own fault held
out of the history, the model still names an action, copied from whichever precedent came
closest, and it is confidently wrong. Two things retrieval already knows say that will
happen: the closest past runs were resolved by different actions, or none of them is close.
Either one makes UNKNOWN the answer, and the model is never asked.

A gate never picks an action. Like a check, it can only turn the answer into UNKNOWN.
"""

from collections.abc import Sequence
from typing import Protocol

from triage.prompt import Source

MIN_SIMILARITY = 0.95
"""The cosine similarity the closest precedent must reach, set from the history runs alone.

Measured with tools/calibrate_abstention.py, never on the questions: each of the 27 history
runs, asked from its evidence alone, scored 0.951 to 0.987 against its closest run of the
same fault and 0.796 to 0.982 against its closest run of another fault. The rule, fixed
before the numbers were seen, is the lowest same-fault score rounded down: 0.95. It refuses
no history run a precedent it has, and 18 of 27 the closest other-fault run. The ranges
overlap where cable_loss, link_down and link_flaky sit (0.97 to 0.98), so similarity alone
can't tell those apart; PrecedentsDisagree is the gate meant for them.
"""


class SourceGate(Protocol):
    """Anything that can decide, from the retrieved precedents alone, not to ask the model."""

    def reason_to_abstain(self, sources: Sequence[Source]) -> str:
        """Say why the precedents give no basis for an answer, or nothing when they do.

        Args:
            sources: The precedents retrieval found, most similar first; never empty.

        Returns:
            The reason to answer UNKNOWN, or an empty string to go on and ask the model.
        """


class PrecedentsDisagree:
    """Abstain when the closest precedents were resolved by different actions.

    Only actions the labels record are compared: a precedent whose action wasn't recorded
    neither agrees nor disagrees, since what was done about it is unknown.
    """

    def reason_to_abstain(self, sources: Sequence[Source]) -> str:
        """Abstain when two recorded actions among the precedents differ.

        Args:
            sources: The precedents, most similar first.

        Returns:
            The actions that disagree, or an empty string when the recorded ones agree.
        """
        actions = list(dict.fromkeys(source.action for source in sources if source.action))
        if len(actions) <= 1:
            return ""
        return (
            f"the closest past runs were resolved by different actions ({', '.join(actions)}), "
            "so there is no one precedent to follow"
        )


class WeakPrecedent:
    """Abstain when even the closest precedent is not very like this run.

    Attributes:
        min_similarity: The cosine similarity the closest precedent must reach.
    """

    def __init__(self, min_similarity: float = MIN_SIMILARITY) -> None:
        """Set the similarity the closest precedent must reach.

        Args:
            min_similarity: The threshold, as a cosine similarity.
        """
        self.min_similarity = min_similarity

    def reason_to_abstain(self, sources: Sequence[Source]) -> str:
        """Abstain when the closest scored precedent falls below the threshold.

        A retriever that doesn't score its sources leaves similarity unknown, and unknown is
        not low: the gate then lets the model answer.

        Args:
            sources: The precedents, most similar first.

        Returns:
            The closest similarity and the threshold, or an empty string when it is met or
            not known.
        """
        scored = [source.similarity for source in sources if source.similarity is not None]
        if not scored:
            return ""
        closest = max(scored)
        if closest >= self.min_similarity:
            return ""
        return (
            f"the closest past run is only {closest:.3f} similar, below "
            f"{self.min_similarity:.2f}, so there is no close precedent"
        )


DEFAULT_GATES: tuple[SourceGate, ...] = (PrecedentsDisagree(), WeakPrecedent())
"""The gates the evaluation's abstention scenarios apply, in order."""


def reason_to_abstain(sources: Sequence[Source], gates: Sequence[SourceGate]) -> str:
    """Ask each gate in turn whether to abstain, and return the first reason given.

    No precedents at all means retrieval wasn't used or the history is empty; the gates then
    stay out of the way, because they judge precedents and there are none to judge.

    Args:
        sources: The precedents retrieval found.
        gates: The gates, asked in order.

    Returns:
        The first gate's reason to answer UNKNOWN, or an empty string to ask the model.
    """
    if not sources:
        return ""
    for gate in gates:
        reason = gate.reason_to_abstain(sources)
        if reason:
            return reason
    return ""
