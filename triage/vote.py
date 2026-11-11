"""The majority vote: answer with the action that resolved most of the closest past runs.

A baseline with no model in it. Retrieval already knows how each precedent was resolved, so
the simplest answer is to count: the action with strictly more votes than any other wins, and
a tie, or no precedent with a recorded action, is UNKNOWN. If the model can't beat this, it
adds nothing beyond retrieval; if it can, the difference is what reading the evidence buys.

The vote goes through the same gates and checks as the model's verdict, so a scenario that
swaps one for the other changes exactly one thing.
"""

from collections import Counter
from collections.abc import Sequence

from triage.abstain import SourceGate, reason_to_abstain
from triage.answer import Verdict, with_past_fixes
from triage.checks import VerdictCheck, apply_checks
from triage.evidence import SavedRun
from triage.pack import DEFAULT_PACK, UNKNOWN, Action, Pack
from triage.prompt import Source

WHERE = "what the closest past runs were fixed by"
"""What a vote says to act on: it knows the precedents' actions, not the run's hardware."""


def _recorded_fix(source: Source, pack: Pack) -> Action:
    """Return the fix a precedent's label records.

    Args:
        source: A precedent with a recorded action.
        pack: The domain pack whose actions the labels record.

    Returns:
        The fix.

    Raises:
        KeyError: If the action isn't on the list; the message says to rebuild the data set.
    """
    if source.action not in pack.keys:
        raise KeyError(
            f"{source.source_id}: its label records the action {source.action!r}, which is "
            "not on the list; rebuild the data set with 'make fault-dataset'"
        )
    return pack.action(source.action)


def majority_vote(sources: Sequence[Source], pack: Pack = DEFAULT_PACK) -> Verdict:
    """Answer with the action most of the precedents were resolved by.

    Only recorded actions vote: a precedent whose action wasn't recorded says nothing about
    what to do, so it is left out rather than counted as a vote for anything.

    Args:
        sources: The precedents, most similar first.
        pack: The domain pack whose actions the precedents record.

    Returns:
        The winning action, citing the precedents that voted for it; UNKNOWN on a tie or
        when no precedent records an action.
    """
    voters = [source for source in sources if source.action]
    if not voters:
        return Verdict(
            fix=UNKNOWN,
            where=WHERE,
            because="no past run with a recorded action was found, so there is nothing to vote on",
        )
    votes = Counter(_recorded_fix(source, pack) for source in voters)
    ranked = votes.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        tied = ", ".join(f"{fix.key} x{count}" for fix, count in ranked)
        return Verdict(
            fix=UNKNOWN,
            where=WHERE,
            because=f"the closest past runs split with no majority ({tied})",
        )
    winner, count = ranked[0]
    return Verdict(
        fix=winner,
        where=WHERE,
        because=f"{count} of the {len(voters)} closest past runs were resolved by {winner.key}",
        cited=[source.source_id for source in voters if source.action == winner.key],
    )


def vote_run(
    run: SavedRun,
    sources: Sequence[Source],
    *,
    checks: tuple[VerdictCheck, ...] = (),
    gates: Sequence[SourceGate] = (),
    pack: Pack = DEFAULT_PACK,
) -> Verdict:
    """Decide what to do about one failed run by majority vote, with the model's gates and checks.

    Args:
        run: The saved run, read only by the checks; its label is never read.
        sources: The precedents retrieval found, most similar first.
        checks: Rules the verdict must agree with, as triage.service.triage_run applies them.
        gates: Rules on the sources alone, applied before the vote, as for the model.
        pack: The domain pack whose actions the precedents record.

    Returns:
        The verdict: UNKNOWN with the gate's reason, or the vote after the checks.
    """
    reason = reason_to_abstain(sources, gates)
    if reason:
        return Verdict.gated(reason)
    return with_past_fixes(apply_checks(run, majority_vote(sources, pack), checks), sources)
