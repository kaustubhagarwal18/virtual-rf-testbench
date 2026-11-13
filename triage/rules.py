"""The rules-only answerer: decide from the check rules alone, with no model and no history.

A baseline with neither a model nor retrieval. The pack's checks refuse actions that
contradict recorded facts; when every action but one is refused, the survivor is the answer.
When zero or more than one survive, there is not enough to decide, and the answer is UNKNOWN.
If the model can't beat this, the rules carry the diagnosis by themselves.

Built like the majority vote (triage.vote): it goes through the same checks afterwards, so a
scenario that swaps one answerer for another changes exactly one thing.
"""

from triage.answer import Verdict
from triage.case import Case
from triage.checks import VerdictCheck, apply_checks
from triage.pack import DEFAULT_PACK, UNKNOWN, UNKNOWN_KEY, Pack

WHERE = "what the check rules decided"
"""What a rules-only answer says to act on: the checks' recorded facts, not a model's read."""


def rules_verdict(
    run: Case, checks: tuple[VerdictCheck, ...], pack: Pack = DEFAULT_PACK
) -> Verdict:
    """Answer with the one action the checks don't refuse, or UNKNOWN.

    Each non-UNKNOWN action is tried as a tentative verdict; the checks either let it stand
    or replace it with UNKNOWN. The actions that survive are counted. One survivor wins; zero
    or more than one means the rules alone can't decide.

    Args:
        run: The case, whole or focused on one failure.
        checks: The rules to apply, from the domain pack.
        pack: The domain pack whose actions are tried.

    Returns:
        The sole surviving action, or UNKNOWN with a reason naming what happened.
    """
    survivors = []
    for action in pack.actions:
        if action.key == UNKNOWN_KEY:
            continue
        trial = Verdict(fix=action, where=WHERE, because="tentative")
        after = apply_checks(run, trial, checks)
        if after.fix.key != UNKNOWN_KEY:
            survivors.append(action)
    if len(survivors) == 1:
        winner = survivors[0]
        return Verdict(
            fix=winner,
            where=WHERE,
            because=f"every other action was refused by the checks, leaving {winner.key}",
        )
    if not survivors:
        return Verdict(
            fix=UNKNOWN,
            where=WHERE,
            because="every action was refused by the checks",
        )
    keys = ", ".join(action.key for action in survivors)
    return Verdict(
        fix=UNKNOWN,
        where=WHERE,
        because=f"the checks left {len(survivors)} actions ({keys}), not one",
    )


def rules_run(
    run: Case,
    *,
    checks: tuple[VerdictCheck, ...] = (),
    pack: Pack = DEFAULT_PACK,
) -> Verdict:
    """Decide what to do about one failed run by the check rules alone.

    The verdict from rules_verdict is then passed through the same checks a model verdict
    would see, so a scenario comparing rules_run with triage_run changes only the answerer.

    Args:
        run: The case, whole or focused on one failure.
        checks: The rules, applied both to select the answer and to review it afterwards.
        pack: The domain pack whose actions and checks are used.

    Returns:
        The verdict, after the checks have reviewed it.
    """
    return apply_checks(run, rules_verdict(run, checks, pack), checks)
