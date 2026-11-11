"""Rules that check the model's verdict against facts the evidence states outright.

A small model has fallback answers: when unsure, it blames the firmware or the timeout, or
copies a precedent's diagnosis whole. But some facts are not judgements, they are in the
results file: which releases the run ran, whether that changed, and whether the instruments
reported an SCPI error. A check turns a verdict that contradicts
such a fact into UNKNOWN. It never picks a different action in its place: that would be a
guess, and a confident wrong action is the failure that costs an engineer hours.
"""

import re
from dataclasses import replace
from typing import Protocol

from triage.answer import Verdict
from triage.evidence import (
    SavedRun,
    failed_level_reading,
    firmware_changed,
    instrument_errors,
    no_signal_note,
)
from triage.pack import UNKNOWN


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
        if verdict.fix.key != "FIRMWARE" or firmware_changed(run) is not False:
            return verdict
        return replace(
            verdict,
            fix=UNKNOWN,
            overruled=(
                "the model chose FIRMWARE, but the firmware is unchanged since the last "
                "passing run, so a firmware regression is not what the evidence shows"
            ),
        )


RELEASE_PATTERN = re.compile(r"\b\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?\b")
"""A release number such as 0.2.0 or 0.2.0-rc2, as the bench's firmware versions are written."""


class ForeignReleaseCheck:
    """Refuse a firmware verdict that blames a release this run didn't run.

    With retrieval, the model sometimes copies a precedent's diagnosis whole, release number
    included: 'release 0.2.0-rc2 doesn't clear the offset' for a run still on 0.1.0. The run
    records which releases it ran, so a verdict naming another one is contradicted by the
    evidence. When the run recorded no versions, nothing can be compared and the verdict is
    left alone.
    """

    def review(self, run: SavedRun, verdict: Verdict) -> Verdict:
        """Overrule FIRMWARE when it names a release that isn't one of the run's own.

        Args:
            run: The run the verdict is about.
            verdict: The model's verdict.

        Returns:
            The verdict, or UNKNOWN with the reason it was overruled.
        """
        versions = run.results.get("firmware_versions") or {}
        if verdict.fix.key != "FIRMWARE" or not versions:
            return verdict
        named = RELEASE_PATTERN.findall(f"{verdict.where} {verdict.because}")
        foreign = [release for release in named if release not in versions.values()]
        if not foreign:
            return verdict
        ran = ", ".join(f"{role} {version}" for role, version in sorted(versions.items()))
        return replace(
            verdict,
            fix=UNKNOWN,
            overruled=(
                f"the model blamed release {foreign[0]}, but this run ran {ran}: the "
                "diagnosis belongs to another run"
            ),
        )


class TimeoutWithInstrumentErrorCheck:
    """Refuse a timeout verdict when the instruments reported an error in the failed tests.

    A slow instrument answers late but accepts the command, so it queues no SCPI error. A
    test that timed out and also reports one, such as -113 "Undefined header", is waiting
    for an answer to a command the instrument refused: raising the timeout would not help.
    """

    def review(self, run: SavedRun, verdict: Verdict) -> Verdict:
        """Overrule TIMEOUT when a failed test reports an instrument error.

        Args:
            run: The run the verdict is about.
            verdict: The model's verdict.

        Returns:
            The verdict, or UNKNOWN with the reason it was overruled.
        """
        errors = instrument_errors(run)
        if verdict.fix.key != "TIMEOUT" or not errors:
            return verdict
        return replace(
            verdict,
            fix=UNKNOWN,
            overruled=(
                f"the model chose TIMEOUT, but the instruments reported {errors[0]}: a "
                "rejected command, not a slow instrument"
            ),
        )


class AlignmentWithoutSignalCheck:
    """Refuse a self-alignment verdict when the analyzer logged that no signal reached it.

    Self-alignment corrects an instrument's own measurement error; it can't bring back a
    signal that never arrives. With retrieval, the model chose ALIGNMENT on a disconnected
    cable, drawn by precedents whose fix reads "reconnected the cable and recalibrated"
    (ADR 0017).
    """

    def review(self, run: SavedRun, verdict: Verdict) -> Verdict:
        """Overrule ALIGNMENT when the analyzer logged no signal.

        Args:
            run: The run the verdict is about.
            verdict: The model's verdict.

        Returns:
            The verdict, or UNKNOWN with the reason it was overruled.
        """
        if verdict.fix.key != "ALIGNMENT" or not no_signal_note(run):
            return verdict
        return replace(
            verdict,
            fix=UNKNOWN,
            overruled=(
                "the model chose ALIGNMENT, but the analyzer logged that no signal reached "
                "it: self-alignment can't restore a missing signal"
            ),
        )


class AlignmentWithoutLevelReadingCheck:
    """Refuse a self-alignment verdict when no level or power reading is out of limits.

    An analyzer's alignment error shifts the levels it reads; it doesn't move a frequency,
    stop a setup step or time out a query. In CI, a run with two faults (a release putting
    the frequency 4 ppm high, and a 3 dB alignment error) had its frequency failures
    answered ALIGNMENT, drawn by the alignment warning in the run's logs (ADR 0020).
    """

    def review(self, run: SavedRun, verdict: Verdict) -> Verdict:
        """Overrule ALIGNMENT when no failed reading is a level or a power.

        Args:
            run: The run the verdict is about, whole or focused on one failure.
            verdict: The model's verdict.

        Returns:
            The verdict, or UNKNOWN with the reason it was overruled.
        """
        if verdict.fix.key != "ALIGNMENT" or failed_level_reading(run):
            return verdict
        return replace(
            verdict,
            fix=UNKNOWN,
            overruled=(
                "the model chose ALIGNMENT, but no level or power reading is out of limits "
                "in this failure: an alignment error shifts levels, not frequencies"
            ),
        )


DEFAULT_CHECKS: tuple[VerdictCheck, ...] = (
    ForeignReleaseCheck(),
    FirmwareUnchangedCheck(),
    TimeoutWithInstrumentErrorCheck(),
    AlignmentWithoutSignalCheck(),
    AlignmentWithoutLevelReadingCheck(),
)
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
