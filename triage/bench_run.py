"""The adapter for this repository's bench runs: a run folder becomes a case.

A bench run is what `make fault` and CI save: the suite's results file (each test with its
outcome, message, SCPI errors and measurements), the instruments' log lines as
`docker compose logs` prints them, and the setup sequence's log. This module reads those
and nothing else; the label beside them is never read.

`load_case` is how the pipeline opens any saved case: a folder with a `case.json` is read
as it is, in the generic format, and a folder with a bench results file is read through
this adapter.
"""

import json
import re
from pathlib import Path
from typing import Any

from triage.case import (
    CASE_FILE,
    OUTCOMES,
    Case,
    CaseError,
    Context,
    Item,
    LogLine,
    Reading,
    Step,
    case_from_dict,
)

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

_LINE_FIELDS = frozenset({"time", "level"})
"""Fields every sequence-log line has, which say nothing about the step."""


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


def _step(fields: dict[str, str]) -> Step:
    """Turn a sequence-log line's fields into a step.

    Args:
        fields: The line's fields.

    Returns:
        The step; an ERROR line is a failed step.
    """
    return Step(
        number=fields.get("step", "?"),
        action=fields.get("action", "unknown"),
        target=fields.get("instrument", "bench"),
        failed=fields.get("level") == "ERROR",
        reason=fields.get("reason", ""),
        params=tuple(
            (name, value)
            for name, value in fields.items()
            if name not in _DESCRIBED_FIELDS and name not in _LINE_FIELDS
        ),
    )


def _log_line(text: str) -> LogLine:
    """Read a `docker compose logs` line, whose JSON record names its level in quotes.

    Args:
        text: The line.

    Returns:
        The line with its level: WARNING or ERROR when the record says so, otherwise INFO.
    """
    for level in ("WARNING", "ERROR"):
        if f'"{level}"' in text:
            return LogLine(text, level)
    return LogLine(text)


def _item(test: dict[str, Any]) -> Item:
    """Turn a test's record in the results file into an item.

    Args:
        test: The record.

    Returns:
        The item, with its SCPI errors and measurements.

    Raises:
        CaseError: If the outcome isn't one of the known values.
    """
    outcome = str(test["outcome"])
    if outcome not in OUTCOMES:
        raise CaseError(
            f"item {test.get('id', '?')!r} has outcome {outcome!r}; expected one of {OUTCOMES}"
        )
    return Item(
        id=str(test["id"]),
        outcome=outcome,
        message=str(test.get("message", "")),
        errors=tuple(str(error) for error in test.get("instrument_errors", [])),
        readings=tuple(
            Reading(
                name=str(measurement["name"]),
                value=measurement["value"],
                unit=str(measurement.get("unit", "")),
                low=measurement.get("limit_low"),
                high=measurement.get("limit_high"),
                margin=measurement.get("margin"),
                passed=bool(measurement["passed"]),
            )
            for measurement in test.get("measurements", [])
        ),
    )


def case_from_bench(
    results: dict[str, Any], logs: list[str], sequence: list[str], folder: Path
) -> Case:
    """Turn a bench run's evidence into a case.

    Args:
        results: The suite's results file, parsed.
        logs: The instruments' log lines.
        sequence: The setup sequence's log lines; empty when no step ran.
        folder: Where the run is saved, or a placeholder for one posted to the API.

    Returns:
        The case.
    """
    return Case(
        folder=folder,
        items=tuple(_item(test) for test in results.get("tests", [])),
        steps=tuple(
            _step(fields) for fields in (sequence_fields(line) for line in sequence) if fields
        ),
        logs=tuple(_log_line(line) for line in logs),
        context=Context(
            versions={
                str(role): str(version)
                for role, version in (results.get("firmware_versions") or {}).items()
            },
            selection=results.get("selection") or None,
            totals=dict(results.get("totals") or {}),
            item_source=RESULTS_FILE,
            step_source=SEQUENCE_FILE,
        ),
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


def _json(path: Path) -> Any:
    """Read a JSON file, turning a parse failure into a CaseError naming it.

    Args:
        path: The file.

    Returns:
        The parsed contents.

    Raises:
        CaseError: If the file isn't valid JSON.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CaseError(f"{path}: not valid JSON: {error.msg} at line {error.lineno}") from error


def load_case(folder: Path) -> Case:
    """Open a saved case: case.json as it is, or a bench run through this adapter.

    Args:
        folder: The case's folder.

    Returns:
        The case; `label.json` is deliberately not read.

    Raises:
        FileNotFoundError: If the folder has neither a case.json nor a results file.
        CaseError: If the file there isn't valid JSON, or a bench results file is missing
            a field or has one of the wrong shape; case.json's own mistakes are raised by
            case_from_dict.
    """
    case_path = folder / CASE_FILE
    if case_path.exists():
        return case_from_dict(_json(case_path), folder)
    results_path = folder / RESULTS_FILE
    if not results_path.exists():
        raise FileNotFoundError(
            f"{folder} has no {CASE_FILE} or {RESULTS_FILE}: it isn't a saved case"
        )
    results = _json(results_path)
    try:
        return case_from_bench(
            results, _lines(folder / LOGS_FILE), _lines(folder / SEQUENCE_FILE), folder
        )
    except KeyError as error:
        raise CaseError(f"{results_path}: a required field is missing: {error}") from error
    except (TypeError, AttributeError, ValueError) as error:
        raise CaseError(f"{results_path}: a field has the wrong shape: {error}") from error
