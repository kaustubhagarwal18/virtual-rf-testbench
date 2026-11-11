"""A case: one thing that went wrong, in a shape that fits any domain.

The pipeline reads cases, never a domain's own files. A case holds items (tests, checks,
transactions, wafer lots: whatever passed or failed), each with an outcome, a message, the
errors it reported and its readings with their limits; the steps that prepared them; the
log lines around them; and the version of each component involved. An adapter turns a
domain's own output into a case: `triage/bench_run.py` reads this repository's bench runs.
Any other system can write `case.json` in the format below and skip the adapter.

The label beside a case (`label.json`, how it was resolved) is never part of the case: it
is the answer, and the case is the question.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CASE_FILE = "case.json"
CASE_FORMAT = "triage-case/1"
"""The format a case.json declares, so a later format can be told apart."""

NOTABLE_LOG_LEVELS = ("WARNING", "ERROR")
"""Log levels worth showing: a healthy run's INFO lines are noise."""


@dataclass(frozen=True)
class Reading:
    """One measured value of an item, with the limits it was held to.

    Attributes:
        name: What was measured.
        value: The reading, as recorded.
        unit: Its unit; empty when it has none.
        low: The lower limit, if any.
        high: The upper limit, if any.
        margin: How far inside (positive) or outside (negative) the limits it was.
        passed: Whether it was within the limits.
    """

    name: str
    value: Any
    unit: str
    low: Any
    high: Any
    margin: Any
    passed: bool


@dataclass(frozen=True)
class Item:
    """One thing that was checked and passed or failed: a test, a check, a transaction.

    Attributes:
        id: What it is, unique within the case.
        outcome: passed, failed, error or skipped.
        message: Why it failed, as it reported; empty when it passed.
        errors: Errors the system under test reported while it ran.
        readings: Its measured values and their limits.
    """

    id: str
    outcome: str
    message: str = ""
    errors: tuple[str, ...] = ()
    readings: tuple[Reading, ...] = ()

    @property
    def failed(self) -> bool:
        """Whether it didn't pass.

        Returns:
            True for any outcome other than passed.
        """
        return self.outcome != "passed"


@dataclass(frozen=True)
class Step:
    """One step of the setup that prepared the items.

    Attributes:
        number: Its place in the sequence, as recorded.
        action: What it did.
        target: What it acted on.
        failed: Whether it failed.
        reason: Why it failed; empty when it didn't.
        params: The named values it used, in the order recorded.
    """

    number: str
    action: str
    target: str
    failed: bool
    reason: str = ""
    params: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class LogLine:
    """One log line, with its level.

    Attributes:
        text: The line as logged.
        level: INFO, WARNING, ERROR or another level name.
    """

    text: str
    level: str = "INFO"

    @property
    def notable(self) -> bool:
        """Whether a person would read it: a warning or an error.

        Returns:
            True for NOTABLE_LOG_LEVELS.
        """
        return self.level in NOTABLE_LOG_LEVELS


@dataclass(frozen=True)
class Focus:
    """What a case focused on one failure (triage.failures.focus) knows about the others.

    Attributes:
        items: How many items failed the way this one did.
        others: Descriptions of the case's other distinct failures.
    """

    items: int
    others: tuple[str, ...] = ()


@dataclass(frozen=True)
class Context:
    """What was true of the whole case: versions, what ran, and where it was read from.

    Attributes:
        versions: Each component's version; empty when not recorded, which is unknown.
        selection: Which items ran (collected, selected, deselected); None when unknown.
        totals: How many items passed, failed, errored and were skipped.
        item_source: The file the items came from, named in the evidence.
        step_source: The file the steps came from, named in the evidence.
    """

    versions: dict[str, str] = field(default_factory=dict)
    selection: dict[str, int] | None = None
    totals: dict[str, int] = field(default_factory=dict)
    item_source: str = CASE_FILE
    step_source: str = CASE_FILE


@dataclass(frozen=True)
class Case:
    """One thing that went wrong, and everything recorded about it.

    Attributes:
        folder: Where it is saved, which is also where its label is.
        items: Every item, passed or not, in the order they ran.
        steps: The setup steps, in order; empty when there were none.
        logs: The log lines, in order.
        context: Versions, what ran, and where it was read from.
        focus: Set when the case is focused on one of its failures.
    """

    folder: Path
    items: tuple[Item, ...] = ()
    steps: tuple[Step, ...] = ()
    logs: tuple[LogLine, ...] = ()
    context: Context = field(default_factory=Context)
    focus: Focus | None = None

    @property
    def failed_items(self) -> list[Item]:
        """The items that didn't pass, in the order they ran.

        Returns:
            Each failed, errored or skipped item.
        """
        return [item for item in self.items if item.failed]

    @property
    def failed_readings(self) -> list[Reading]:
        """The out-of-limit readings of the items that didn't pass.

        Returns:
            Each failed reading, in the order the items ran.
        """
        return [
            reading for item in self.failed_items for reading in item.readings if not reading.passed
        ]

    @property
    def failed_steps(self) -> list[Step]:
        """The setup steps that failed, in order.

        Returns:
            Each failed step.
        """
        return [step for step in self.steps if step.failed]


def _reading(raw: dict[str, Any]) -> Reading:
    """Read one reading from case.json.

    Args:
        raw: The reading's object.

    Returns:
        The reading.
    """
    return Reading(
        name=str(raw["name"]),
        value=raw.get("value"),
        unit=str(raw.get("unit", "")),
        low=raw.get("low"),
        high=raw.get("high"),
        margin=raw.get("margin"),
        passed=bool(raw["passed"]),
    )


def case_from_dict(raw: dict[str, Any], folder: Path) -> Case:
    """Build a case from case.json's contents.

    Args:
        raw: The parsed file.
        folder: Where it was read from.

    Returns:
        The case.

    Raises:
        ValueError: If the file declares another format; the message names both.
    """
    if raw.get("format") != CASE_FORMAT:
        raise ValueError(
            f"{folder / CASE_FILE} declares format {raw.get('format')!r}, not {CASE_FORMAT!r}"
        )
    return Case(
        folder=folder,
        items=tuple(
            Item(
                id=str(item["id"]),
                outcome=str(item["outcome"]),
                message=str(item.get("message", "")),
                errors=tuple(str(error) for error in item.get("errors", [])),
                readings=tuple(_reading(reading) for reading in item.get("readings", [])),
            )
            for item in raw.get("items", [])
        ),
        steps=tuple(
            Step(
                number=str(step.get("number", "?")),
                action=str(step.get("action", "unknown")),
                target=str(step.get("target", "")),
                failed=bool(step.get("failed", False)),
                reason=str(step.get("reason", "")),
                params=tuple(
                    (str(name), str(value)) for name, value in step.get("params", {}).items()
                ),
            )
            for step in raw.get("steps", [])
        ),
        logs=tuple(
            LogLine(str(line["text"]), str(line.get("level", "INFO")))
            for line in raw.get("logs", [])
        ),
        context=Context(
            versions={str(name): str(version) for name, version in raw.get("versions", {}).items()},
            selection=raw.get("selection"),
            totals=dict(raw.get("totals", {})),
            item_source=str(raw.get("item_source", CASE_FILE)),
            step_source=str(raw.get("step_source", CASE_FILE)),
        ),
    )


def case_to_dict(case: Case) -> dict[str, Any]:
    """Write a case as case.json's contents.

    Args:
        case: The case; a focus isn't saved, since it belongs to one triage, not the case.

    Returns:
        The contents, ready for json.dumps.
    """
    return {
        "format": CASE_FORMAT,
        "items": [
            {
                "id": item.id,
                "outcome": item.outcome,
                "message": item.message,
                "errors": list(item.errors),
                "readings": [
                    {
                        "name": reading.name,
                        "value": reading.value,
                        "unit": reading.unit,
                        "low": reading.low,
                        "high": reading.high,
                        "margin": reading.margin,
                        "passed": reading.passed,
                    }
                    for reading in item.readings
                ],
            }
            for item in case.items
        ],
        "steps": [
            {
                "number": step.number,
                "action": step.action,
                "target": step.target,
                "failed": step.failed,
                "reason": step.reason,
                "params": dict(step.params),
            }
            for step in case.steps
        ],
        "logs": [{"text": line.text, "level": line.level} for line in case.logs],
        "versions": dict(case.context.versions),
        "selection": case.context.selection,
        "totals": dict(case.context.totals),
        "item_source": case.context.item_source,
        "step_source": case.context.step_source,
    }


def save_case(case: Case, folder: Path) -> Path:
    """Write a case to a folder as case.json.

    Args:
        case: The case.
        folder: Where to write it; created if needed.

    Returns:
        The file written.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / CASE_FILE
    path.write_text(json.dumps(case_to_dict(case), indent=1) + "\n", encoding="utf-8")
    return path
