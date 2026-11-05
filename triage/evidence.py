"""Reading a saved bench run, and finding the one thing that went wrong first.

A run with nineteen errors has one cause and eighteen consequences. Triage starts where a
person starts: the first step that failed. Everything else is context for it.

The evidence a run carries is its results file, the instruments' logs and the setup
sequence's log. The label beside them is never read here: it is the answer, and this module
is the question.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

RESULTS_FILE = "results.json"
LOGS_FILE = "logs.txt"
SEQUENCE_FILE = "sequence.log"

_SEQUENCE_FIELDS = re.compile(r"(\w+)=(.*?)(?=\s+\w+=|$)")
"""One key=value field of a sequence-log line; values may contain spaces."""

_SEQUENCE_LINE = re.compile(r"^(?P<time>\S+)\s+(?P<level>\w+)\s+(?P<fields>.*)$")
"""A sequence-log line: a timestamp, a level, then its fields."""

REASON_FIELD = " reason="
"""Where a step's reason begins. The sequencer writes it last, and everything after it is
free text that may itself contain '=', so it is read whole rather than as fields."""

_DESCRIBED_FIELDS = frozenset({"step", "action", "instrument", "reason", "result"})
"""Fields named in a step's description, so the rest are its parameters."""


@dataclass(frozen=True)
class SavedRun:
    """A run's evidence, as saved by `make fault`.

    Attributes:
        folder: Where the run is saved.
        results: The bench suite's results file.
        logs: The instruments' log lines.
        sequence: The setup sequence's log lines; empty when no step ever ran.
    """

    folder: Path
    results: dict[str, Any]
    logs: list[str] = field(default_factory=list)
    sequence: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, folder: Path) -> Self:
        """Read a saved run's evidence, and nothing else.

        Args:
            folder: The run's folder.

        Returns:
            The run, with whichever artefacts it has; `label.json` is deliberately not read.

        Raises:
            FileNotFoundError: If the run has no results file, which every run must have.
        """
        results_path = folder / RESULTS_FILE
        if not results_path.exists():
            raise FileNotFoundError(f"{folder} has no {RESULTS_FILE}: it isn't a saved run")
        return cls(
            folder=folder,
            results=json.loads(results_path.read_text(encoding="utf-8")),
            logs=_lines(folder / LOGS_FILE),
            sequence=_lines(folder / SEQUENCE_FILE),
        )


def _lines(path: Path) -> list[str]:
    """Read a text artefact's lines, treating an absent file as no lines.

    Args:
        path: The file.

    Returns:
        Its lines without endings, or nothing when the run doesn't have it.
    """
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def sequence_fields(line: str) -> dict[str, str]:
    """Read one sequence-log line into its fields.

    Args:
        line: The line, for example '17:03 ERROR step=11 action=verify level offset ...'.

    Returns:
        The fields, with 'time' and 'level' included; empty if the line has no fields. The
        reason keeps any '=' it contains, so an error message isn't read as further fields.
    """
    match = _SEQUENCE_LINE.match(line)
    if match is None:
        return {}
    fields = {"time": match["time"], "level": match["level"]}
    described, separator, reason = match["fields"].partition(REASON_FIELD)
    fields.update(dict(_SEQUENCE_FIELDS.findall(described)))
    if separator:
        fields["reason"] = reason
    return fields


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


def failed_steps(run: SavedRun) -> list[dict[str, str]]:
    """List the setup-sequence steps that failed.

    Args:
        run: The run.

    Returns:
        The fields of each ERROR line, in the order logged.
    """
    return [
        fields
        for fields in (sequence_fields(line) for line in run.sequence)
        if fields.get("level") == "ERROR"
    ]


def failed_tests(run: SavedRun) -> list[dict[str, Any]]:
    """List the tests that didn't pass, in the order they ran.

    Args:
        run: The run.

    Returns:
        Each failed or errored test's record.
    """
    return [test for test in run.results.get("tests", []) if test["outcome"] != "passed"]


def first_failure(run: SavedRun) -> FirstFailure | None:
    """Find the one thing that went wrong first, which the rest of the run follows from.

    A failed setup step comes first: when the sequence that prepares the instruments stops,
    every test after it fails for that reason and not for its own.

    Args:
        run: The run.

    Returns:
        The first failure, or None when every test passed.
    """
    tests = failed_tests(run)
    steps = failed_steps(run)
    if steps:
        step = steps[0]
        return FirstFailure(
            source=SEQUENCE_FILE,
            where=(
                f"step {step.get('step', '?')} ({step.get('action', 'unknown')}) "
                f"on the {step.get('instrument', 'bench')}"
            ),
            detail=step.get("reason", ""),
            parameters={
                name: value
                for name, value in step.items()
                if name not in _DESCRIBED_FIELDS and name not in {"time", "level"}
            },
            consequences=max(len(tests) - 1, 0),
        )
    if not tests:
        return None
    test = tests[0]
    return FirstFailure(
        source=RESULTS_FILE,
        where=test["id"].split("::")[-1],
        detail=test["message"],
        parameters={
            measurement["name"]: (
                f"{measurement['value']} {measurement['unit']}, limit "
                f"{measurement['limit_low']} to {measurement['limit_high']}, "
                f"margin {measurement['margin']}"
            )
            for measurement in test.get("measurements", [])
            if not measurement["passed"]
        },
        consequences=len(tests) - 1,
    )


ERROR_ONLY = "error-only"
"""The failing test's message and nothing else: what a person pastes into a chat window."""

FULL = "full"
"""Everything the run recorded: the first failure, what changed, and the notable log lines."""

LEVELS = (ERROR_ONLY, FULL)
"""The evidence levels the evaluation compares."""

NOTABLE_LOG_LEVELS = ("WARNING", "ERROR")
"""Instrument log lines worth showing: a healthy run's INFO lines are noise."""

MAX_NOTABLE_LOGS = 6
"""How many notable log lines to show, newest last."""

BASELINE_VERSION = "0.1.0"
"""The release the bench normally runs, and the one the last passing run used."""


def firmware_note(run: SavedRun) -> str:
    """Say what firmware the run used, and whether that changed.

    A firmware regression and a bench fault can look identical in the measurements; whether
    the version moved is what separates them, so it is stated rather than left to be noticed.

    Args:
        run: The run.

    Returns:
        One line naming the versions and whether they differ from the last passing run.
    """
    versions = run.results.get("firmware_versions", {})
    if not versions:
        text = "not recorded"
    else:
        text = ", ".join(f"{role} {version}" for role, version in sorted(versions.items()))
    changed = any(version != BASELINE_VERSION for version in versions.values())
    moved = (
        "changed since the last passing run" if changed else "unchanged since the last passing run"
    )
    return f"Firmware: {text} ({moved})."


def notable_logs(run: SavedRun) -> list[str]:
    """Pick the instrument log lines a person would actually read.

    Args:
        run: The run.

    Returns:
        The last few warnings and errors, or nothing when the instruments logged none.
    """
    notable = [
        line for line in run.logs if any(f'"{level}"' in line for level in NOTABLE_LOG_LEVELS)
    ]
    return notable[-MAX_NOTABLE_LOGS:]


def selection_note(run: SavedRun) -> str:
    """Say which tests ran, so a subset run can't read like a complete one.

    Args:
        run: The run.

    Returns:
        One line with the counts, or an empty string when the run didn't record them.
    """
    selection = run.results.get("selection")
    if not selection:
        return ""
    totals = run.results.get("totals", {})
    return (
        f"Tests: {selection['selected']} of {selection['collected']} selected"
        f" ({selection['deselected']} deselected); {totals.get('passed', 0)} passed,"
        f" {totals.get('failed', 0)} failed, {totals.get('error', 0)} errored."
    )


def error_only_evidence(run: SavedRun) -> str:
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


def full_evidence(run: SavedRun) -> str:
    """Build everything the run recorded, led by the failure the rest follows from.

    Args:
        run: The run.

    Returns:
        The evidence as text, ready to put in a prompt.
    """
    failure = first_failure(run)
    if failure is None:
        return "Every test passed."
    # Only the reason's first line: where there are parameters they carry the same numbers
    # more precisely, and a prompt shouldn't say everything twice.
    reason = failure.detail.splitlines()[0] if failure.detail else ""
    lines = [
        f"First failure: {failure.where} (found in {failure.source}).",
        f"Reason: {reason}",
    ]
    for name, value in failure.parameters.items():
        lines.append(f"  {name}: {value}")
    if failure.consequences:
        lines.append(
            f"{failure.consequences} further tests failed after it, which may be consequences."
        )
    lines.append(firmware_note(run))
    selection = selection_note(run)
    if selection:
        lines.append(selection)
    logs = notable_logs(run)
    lines.append(f"Instrument logs: {len(run.logs)} lines, {len(logs)} warnings or errors.")
    lines.extend(f"  {line}" for line in logs)
    return "\n".join(lines)


def build_evidence(run: SavedRun, level: str = FULL) -> str:
    """Build a run's evidence at the level asked for.

    Args:
        run: The run.
        level: ERROR_ONLY or FULL.

    Returns:
        The evidence as text.

    Raises:
        ValueError: If the level isn't one the evaluation compares.
    """
    if level == ERROR_ONLY:
        return error_only_evidence(run)
    if level == FULL:
        return full_evidence(run)
    raise ValueError(f"unknown evidence level {level!r}; expected one of {LEVELS}")
