"""Building the evidence for a case, led by the one thing that went wrong first.

A run with nineteen errors has one cause and eighteen consequences. Triage starts where a
person starts: the first step that failed. Everything else is context for it.

The evidence is built from a case (triage.case), whatever system it came from. The label
beside it is never read here: it is the answer, and this module is the question.
"""

from dataclasses import dataclass, field

from triage.case import Case
from triage.pack import DEFAULT_PACK, VERSIONS_LINE, Pack


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


def instrument_errors(run: Case) -> list[str]:
    """List the SCPI errors the instruments reported in the tests that didn't pass.

    Args:
        run: The run.

    Returns:
        Each error, such as '-113,"Undefined header"', in the order the tests ran.
    """
    return [error for item in run.failed_items for error in item.errors]


def errors_note(run: Case, pack: Pack = DEFAULT_PACK) -> str:
    """Say which errors the failed items reported, if any.

    A rejected command can surface in the test as a timeout on the query after it, so the
    first failure reads as a timeout while the instrument's error says what happened. This
    line states the error on its own, and full_evidence puts it first.

    Args:
        run: The case.
        pack: The domain pack, which words the line.

    Returns:
        One line naming each distinct error and how many failed items reported one, or an
        empty string when none did.
    """
    errors = list(dict.fromkeys(instrument_errors(run)))
    if not errors:
        return ""
    tests = run.failed_items
    reporting = sum(1 for item in tests if item.errors)
    return pack.evidence.errors.format(
        errors="; ".join(errors), reporting=reporting, failed=len(tests)
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


def versions_note(run: Case, pack: Pack = DEFAULT_PACK) -> str:
    """Say what versions the case ran, and whether that changed since the last passing run.

    A regression and a fault elsewhere can look identical in the readings; whether a version
    moved is what separates them, so it is stated rather than left to be noticed. The
    comparison uses the pack's versions fact; without one, the versions are only listed.

    Args:
        run: The case.
        pack: The domain pack, which words the line and names the baseline.

    Returns:
        One line naming the versions and whether they differ from the baseline.
    """
    wording = pack.evidence
    versions = run.context.versions
    if not versions:
        return wording.versions_unrecorded
    text = ", ".join(f"{role} {version}" for role, version in sorted(versions.items()))
    fact = pack.facts.get(wording.versions_fact or "")
    if fact is not None and fact(run, "") is not None:
        return wording.versions_unchanged.format(versions=text)
    return wording.versions_changed.format(versions=text)


def notes(
    run: Case, place: str, pack: Pack = DEFAULT_PACK, leave_out: frozenset[str] = frozenset()
) -> list[str]:
    """State the pack's notes for one place whose facts hold.

    Args:
        run: The case.
        place: 'lead' or 'focus'.
        pack: The domain pack, which lists the notes.
        leave_out: Names of notes to leave out, for an ablation.

    Returns:
        Each note's sentence, in the pack's order.
    """
    lines = []
    for note in pack.evidence.notes:
        if note.place != place or note.name in leave_out:
            continue
        values = pack.facts[note.fact](run, "")
        if values is not None:
            lines.append(note.say.format(**values))
    return lines


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
        f"Tests: {selection.get('selected', '?')} of {selection.get('collected', '?')} selected"
        f" ({selection.get('deselected', '?')} deselected); {totals.get('passed', 0)} passed,"
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


MAX_OTHER_FAILURES = 5
"""How many of a run's other failures a focused run's evidence names."""


def _consequence_lines(
    run: Case, failure: FirstFailure, pack: Pack, leave_out: frozenset[str]
) -> list[str]:
    """Say what else failed besides the first failure.

    A whole case says how many items failed after it. A case focused on one failure
    (triage.failures.focus) says how many items failed its way, states the pack's focus
    notes, and names the other failures.

    Args:
        run: The case, whole or focused.
        failure: Its first failure.
        pack: The domain pack, which lists the focus notes.
        leave_out: Names of notes to leave out, for an ablation.

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
    lines += notes(run, "focus", pack, leave_out)
    others = list(focused.others)
    if others:
        named = "; ".join(others[:MAX_OTHER_FAILURES])
        more = len(others) - MAX_OTHER_FAILURES
        named += f"; and {more} more" if more > 0 else ""
        lines.append(f"The run's other distinct failures, triaged separately: {named}.")
    return lines


def full_evidence(
    run: Case, pack: Pack = DEFAULT_PACK, leave_out: frozenset[str] = frozenset()
) -> str:
    """Build everything the case recorded, led by the failure the rest follows from.

    The pack's lead notes and any errors the failed items reported come before the first
    failure, because the first failure is often what they caused: a path loss of 110 dB
    with no signal, or a timeout after a rejected command.

    Args:
        run: The case.
        pack: The domain pack, which words the lines that name its things.
        leave_out: Lines to leave out, for an ablation: a note's name, or VERSIONS_LINE.

    Returns:
        The evidence as text, ready to put in a prompt.
    """
    failure = first_failure(run)
    if failure is None:
        return "Every test passed."
    # Only the reason's first line: where there are parameters they carry the same numbers
    # more precisely, and a prompt shouldn't say everything twice.
    reason = failure.detail.splitlines()[0] if failure.detail else ""
    lines = notes(run, "lead", pack, leave_out)
    errors = errors_note(run, pack)
    if errors:
        lines.append(errors)
    lines += [
        f"First failure: {failure.where} (found in {failure.source}).",
        f"Reason: {reason}",
    ]
    for name, value in failure.parameters.items():
        lines.append(f"  {name}: {value}")
    lines += _consequence_lines(run, failure, pack, leave_out)
    if VERSIONS_LINE not in leave_out:
        lines.append(versions_note(run, pack))
    selection = selection_note(run)
    if selection:
        lines.append(selection)
    logs = notable_logs(run)
    lines.append(f"{pack.evidence.logs}: {len(run.logs)} lines, {len(logs)} warnings or errors.")
    lines.extend(f"  {line}" for line in logs)
    return "\n".join(lines)


SIGNAL_NOTE = "no_signal"
"""The RF pack's note the "no signal line" ablation leaves out."""

_LEFT_OUT: dict[str, frozenset[str]] = {
    FULL: frozenset(),
    FULL_WITHOUT_FIRMWARE: frozenset({VERSIONS_LINE}),
    FULL_WITHOUT_SIGNAL: frozenset({SIGNAL_NOTE}),
}
"""What each full level leaves out: nothing, or one line for an ablation."""


def build_evidence(run: Case, level: str = FULL, pack: Pack = DEFAULT_PACK) -> str:
    """Build a case's evidence at the level asked for.

    Args:
        run: The case.
        level: One of EVALUATED_LEVELS.
        pack: The domain pack, which words the lines that name its things.

    Returns:
        The evidence as text.

    Raises:
        ValueError: If the level isn't one this module builds.
    """
    if level == ERROR_ONLY:
        return error_only_evidence(run)
    left_out = _LEFT_OUT.get(level)
    if left_out is None:
        raise ValueError(f"unknown evidence level {level!r}; expected one of {EVALUATED_LEVELS}")
    return full_evidence(run, pack, left_out)
