"""Rules that check the model's verdict against facts the evidence states outright.

A small model has a fallback answer: when unsure, it blames the firmware. But whether the
firmware changed is not a judgement, it is in the results file. A check turns a verdict that
contradicts such a fact into UNKNOWN. It never picks a different action in its place: that
would be a guess, and a confident wrong action is the failure that costs an engineer hours.
"""

from dataclasses import replace
from typing import Protocol

from triage.answer import Verdict
from triage.evidence import SavedRun, firmware_changed
from triage.prompt import Fix


class VerdictCheck(Protocol):
    """Anything that can review a verdict against the run it is about."""

    def review(self, run: SavedRun, verdict: Verdict) -> Verdict:
        """Return the verdict, or UNKNOWN in its place if it contradicts the evidence.

        Args:
            run: The run the verdict is about.
            verdict: The model's verdict.

        Returns:
            The verdict unchanged, or an UNKNOWN verdict that says why it was overruled.
        """


class FirmwareUnchangedCheck:
    """Refuse a firmware verdict when the firmware is the same as the last passing run's.

    A regression needs a release that changed. When the versions are not recorded, whether
    they changed is unknown, so the verdict is left alone rather than overruled on a guess.
    """

    def review(self, run: SavedRun, verdict: Verdict) -> Verdict:
        """Overrule FIRMWARE when the run's firmware is known to be unchanged.

        Args:
            run: The run the verdict is about.
            verdict: The model's verdict.

        Returns:
            The verdict, or UNKNOWN with the reason it was overruled.
        """
        if verdict.fix is not Fix.FIRMWARE or firmware_changed(run) is not False:
            return verdict
        return replace(
            verdict,
            fix=Fix.UNKNOWN,
            overruled=(
                "the model chose FIRMWARE, but the firmware is unchanged since the last "
                "passing run, so a firmware regression is not what the evidence shows"
            ),
        )


DEFAULT_CHECKS: tuple[VerdictCheck, ...] = (FirmwareUnchangedCheck(),)
"""The checks the service applies to every verdict."""


def apply_checks(run: SavedRun, verdict: Verdict, checks: tuple[VerdictCheck, ...]) -> Verdict:
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
