"""Building the evidence for a case, led by the one thing that went wrong first.

A run with nineteen errors has one cause and eighteen consequences. Triage starts where a
person starts: the first step that failed. Everything else is context for it.

The evidence is built from a case (triage.case), whatever system it came from. The label
beside it is never read here: it is the answer, and this module is the question.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from triage.case import Case, Reading


@dataclass(frozen=True)
class FirstFailure:
    """The first thing that went wrong in a run, and where it was found.

    Attributes:
        source: Which artefact it came from, so a reader knows where to look.
        where: What failed, for example a sequence step or a test's id.
        detail: Why, as far as that artefact says.
        parameters: The named values the failing step or measurement involved.
        consequences: How many other tests failed or errored after it.
    """

    source: str
    where: str
    detail: str
    parameters: dict[str, str] = field(default_factory=dict)
    consequences: int = 0


LEVEL_UNITS = frozenset({"dB", "dBm"})
"""Units of absolute level and power readings, which an analyzer's alignment error shifts.
A relative level (dBc) and a frequency (Hz) are left as they were."""


def failed_readings(run: Case) -> list[Reading]:
    """List the out-of-limit readings in the items that didn't pass.

    Args:
        run: The case.

    Returns:
        Each failed reading, in the order the items ran.
    """
    return [reading for item in run.failed_items for reading in item.readings if not reading.passed]


def failed_level_reading(run: Case) -> bool:
    """Say whether any level or power reading is out of limits in the failed tests.

    Args:
        run: The run.

    Returns:
        True when a failed reading is in dB or dBm.
    """
    return any(reading.unit in LEVEL_UNITS for reading in failed_readings(run))


def instrument_errors(run: Case) -> list[str]:
    """List the SCPI errors the instruments reported in the tests that didn't pass.

    Args:
        run: The run.

    Returns:
        Each error, such as '-113,"Undefined header"', in the order the tests ran.
    """
    return [error for item in run.failed_items for error in item.errors]


def instrument_error_note(run: Case) -> str:
    """Say which SCPI errors the instruments reported in the failed tests, if any.

    A rejected command can surface in the test as a timeout on the query after it, so the
    first failure reads as a timeout while the instrument's error says what happened. This
    line states the error on its own, and full_evidence puts it first.

    Args:
        run: The run.

    Returns:
        One line naming each distinct error and how many failed tests reported one, or an
        empty string when none did.
    """
    errors = list(dict.fromkeys(instrument_errors(run)))
    if not errors:
        return ""
    tests = run.failed_items
    reporting = sum(1 for item in tests if item.errors)
    return (
        f"Instrument error: {'; '.join(errors)}, reported by {reporting} of {len(tests)} "
        "failed tests."
    )


def first_failure(run: Case) -> FirstFailure | None:
    """Find the one thing that went wrong first, which the rest of the run follows from.

    A failed setup step comes first: when the sequence that prepares the instruments stops,
    every test after it fails for that reason and not for its own.

    Args:
        run: The run.

    Returns:
        The first failure, or None when every test passed.
    """
    tests = run.failed_items
    steps = run.failed_steps
    if steps:
        step = steps[0]
        return FirstFailure(
            source=run.context.step_source,
            where=f"step {step.number} ({step.action})"
            + (f" on the {step.target}" if step.target else ""),
            detail=step.reason,
            parameters=dict(step.params),
            consequences=max(len(tests) - 1, 0),
        )
    if not tests:
        return None
    test = tests[0]
    return FirstFailure(
        source=run.context.item_source,
        where=test.id.split("::")[-1],
        detail=test.message,
        parameters={
            reading.name: (
                f"{reading.value} {reading.unit}, limit {reading.low} to {reading.high}, "
                f"margin {reading.margin}"
            )
            for reading in test.readings
            if not reading.passed
        },
        consequences=len(tests) - 1,
    )


ERROR_ONLY = "error-only"
"""The failing test's message and nothing else: what a person pastes into a chat window."""

FULL = "full"
"""Everything the run recorded: the first failure, what changed, and the notable log lines."""

FULL_WITHOUT_FIRMWARE = "full-without-firmware"
"""FULL minus its firmware line: an ablation, to measure what that one line does."""

FULL_WITHOUT_SIGNAL = "full-without-signal"
"""FULL minus its "No signal" line: an ablation, to measure what that one line does."""

LEVELS = (ERROR_ONLY, FULL)
"""The evidence levels a caller may ask for."""

EVALUATED_LEVELS = (*LEVELS, FULL_WITHOUT_FIRMWARE, FULL_WITHOUT_SIGNAL)
"""Every level the evaluation can score, the ablations included."""

MAX_NOTABLE_LOGS = 6
"""How many notable log lines to show, newest last."""

BASELINE_VERSION = "0.1.0"
"""The release the bench normally runs, and the one the last passing run used."""


def firmware_changed(run: Case) -> bool | None:
    """Say whether the run's firmware differs from the last passing run's.

    Args:
        run: The run.

    Returns:
        True if any instrument runs another release, False if none does, and None when the
        run recorded no versions: unknown is not the same as unchanged.
    """
    versions = run.context.versions
    if not versions:
        return None
    return any(version != BASELINE_VERSION for version in versions.values())


def firmware_note(run: Case) -> str:
    """Say what firmware the run used, and whether that changed.

    A firmware regression and a bench fault can look identical in the measurements; whether
    the version moved is what separates them, so it is stated rather than left to be noticed.

    When nothing changed, the line doesn't use the word firmware: ADR 0013's ablation found
    that "Firmware: … (unchanged …)" pulled the model to FIRMWARE on a run that ruled it out.
    It states the versions and that they match, which is all the evidence there is.

    Args:
        run: The run.

    Returns:
        One line naming the versions and whether they differ from the last passing run.
    """
    changed = firmware_changed(run)
    if changed is None:
        return (
            "Firmware: not recorded, so whether it changed since the last passing run is unknown."
        )
    versions = run.context.versions
    text = ", ".join(f"{role} {version}" for role, version in sorted(versions.items()))
    if not changed:
        return f"Instruments: {text}, the same as the last passing run."
    return f"Firmware: {text} (changed since the last passing run)."


NO_SIGNAL_LOG = "No signal reaching the analyzer"
"""What the analyzer logs for a reading that saw no signal; a test checks it still does."""


def no_signal_note(run: Case) -> str:
    """Say how often the analyzer logged that no signal reached it, if it did.

    The log lines are in the evidence already, but as the last few of many. A small model
    read a disconnected cable's 110 dB path loss as a calibration problem; this line states
    the fact the logs record, the way instrument_error_note states an SCPI error.

    Args:
        run: The run.

    Returns:
        One line with the count, or an empty string when the analyzer never logged it.
    """
    count = sum(1 for line in run.logs if NO_SIGNAL_LOG in line.text)
    if not count:
        return ""
    times = "once" if count == 1 else f"{count} times"
    return f'No signal: the analyzer logged "{NO_SIGNAL_LOG}" {times}.'


def notable_logs(run: Case) -> list[str]:
    """Pick the instrument log lines a person would actually read.

    Args:
        run: The run.

    Returns:
        The last few warnings and errors, or nothing when the instruments logged none.
    """
    notable = [line.text for line in run.logs if line.notable]
    return notable[-MAX_NOTABLE_LOGS:]


def selection_note(run: Case) -> str:
    """Say which tests ran, so a subset run can't read like a complete one.

    Args:
        run: The run.

    Returns:
        One line with the counts, or an empty string when the run didn't record them.
    """
    selection = run.context.selection
    if not selection:
        return ""
    totals = run.context.totals
    return (
        f"Tests: {selection['selected']} of {selection['collected']} selected"
        f" ({selection['deselected']} deselected); {totals.get('passed', 0)} passed,"
        f" {totals.get('failed', 0)} failed, {totals.get('error', 0)} errored."
    )


def error_only_evidence(run: Case) -> str:
    """Build the smallest evidence there is: the first failure's message.

    Args:
        run: The run.

    Returns:
        The message, or a line saying every test passed.
    """
    failure = first_failure(run)
    if failure is None:
        return "Every test passed."
    return failure.detail or failure.where


NO_LEVEL_READING = "No level or power reading is out of limits in this failure."
"""Said of a focused failure whose out-of-limit readings are all frequencies or relative
levels: the run's logs may warn about another failure's levels, such as an analyzer's
alignment, and that warning is not this failure's cause."""

MAX_OTHER_FAILURES = 5
"""How many of a run's other failures a focused run's evidence names."""


def _consequence_lines(run: Case, failure: FirstFailure) -> list[str]:
    """Say what else failed besides the first failure.

    A whole run says how many tests failed after it. A run focused on one failure
    (triage.failures.focus) says how many tests failed its way, that no level reading is
    out of limits when only frequencies are, and names the other failures.

    Args:
        run: The run, whole or focused.
        failure: Its first failure.

    Returns:
        The lines; none when nothing else failed.
    """
    focused = run.focus
    if focused is None:
        if not failure.consequences:
            return []
        return [f"{failure.consequences} further tests failed after it, which may be consequences."]
    count = focused.items
    lines = [f"{count} {'test' if count == 1 else 'tests'} failed this way."]
    if failed_readings(run) and not failed_level_reading(run):
        lines.append(NO_LEVEL_READING)
    others = list(focused.others)
    if others:
        named = "; ".join(others[:MAX_OTHER_FAILURES])
        more = len(others) - MAX_OTHER_FAILURES
        named += f"; and {more} more" if more > 0 else ""
        lines.append(f"The run's other distinct failures, triaged separately: {named}.")
    return lines


def full_evidence(run: Case, firmware_line: bool = True, signal_line: bool = True) -> str:
    """Build everything the run recorded, led by the failure the rest follows from.

    A missing signal and an SCPI error the instruments reported come before the first
    failure, because the first failure is often what they caused: a path loss of 110 dB,
    or a timeout.

    Args:
        run: The run.
        firmware_line: Whether to state the instruments' versions; False only for the ablation.
        signal_line: Whether to state a missing signal; False only for the ablation.

    Returns:
        The evidence as text, ready to put in a prompt.
    """
    failure = first_failure(run)
    if failure is None:
        return "Every test passed."
    # Only the reason's first line: where there are parameters they carry the same numbers
    # more precisely, and a prompt shouldn't say everything twice.
    reason = failure.detail.splitlines()[0] if failure.detail else ""
    signal = no_signal_note(run) if signal_line else ""
    lines = [note for note in (signal, instrument_error_note(run)) if note]
    lines += [
        f"First failure: {failure.where} (found in {failure.source}).",
        f"Reason: {reason}",
    ]
    for name, value in failure.parameters.items():
        lines.append(f"  {name}: {value}")
    lines += _consequence_lines(run, failure)
    if firmware_line:
        lines.append(firmware_note(run))
    selection = selection_note(run)
    if selection:
        lines.append(selection)
    logs = notable_logs(run)
    lines.append(f"Instrument logs: {len(run.logs)} lines, {len(logs)} warnings or errors.")
    lines.extend(f"  {line}" for line in logs)
    return "\n".join(lines)


def _full_without_firmware(run: Case) -> str:
    """Build the full evidence without its firmware line, for the ablation.

    Args:
        run: The run.

    Returns:
        The evidence as text.
    """
    return full_evidence(run, firmware_line=False)


def _full_without_signal(run: Case) -> str:
    """Build the full evidence without its "No signal" line, for the ablation.

    Args:
        run: The run.

    Returns:
        The evidence as text.
    """
    return full_evidence(run, signal_line=False)


_BUILDERS: dict[str, Callable[[Case], str]] = {
    ERROR_ONLY: error_only_evidence,
    FULL: full_evidence,
    FULL_WITHOUT_FIRMWARE: _full_without_firmware,
    FULL_WITHOUT_SIGNAL: _full_without_signal,
}
"""How each evidence level is built; a new level is a new entry, not a new branch."""


def build_evidence(run: Case, level: str = FULL) -> str:
    """Build a run's evidence at the level asked for.

    Args:
        run: The run.
        level: One of EVALUATED_LEVELS.

    Returns:
        The evidence as text.

    Raises:
        ValueError: If the level isn't one this module builds.
    """
    builder = _BUILDERS.get(level)
    if builder is None:
        raise ValueError(f"unknown evidence level {level!r}; expected one of {EVALUATED_LEVELS}")
    return builder(run)
