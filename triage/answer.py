"""The model's answer: its shape, how it is read, and what makes it unusable.

An answer is rejected rather than guessed at. A reply that doesn't parse, names an action
that isn't on the list, or cites a source it was never given is a failure of the run, not
something to interpret generously: interpreting it would flatter the result.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from triage.prompt import Fix

VERDICT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "fix": {"type": "string", "enum": [fix.name for fix in Fix]},
        "where": {"type": "string"},
        "because": {"type": "string"},
        "cited": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["fix", "where", "because", "cited"],
}
"""The answer's shape as a JSON schema, for a model that can be constrained to it.

The action is an enum of the keys offered, so a constrained reply can't name an action off
the list or leave it empty. parse_verdict still checks everything: the schema narrows what
the model can say, it doesn't replace reading it strictly.
"""


class MalformedAnswerError(Exception):
    """The model's reply can't be used; the message says what was wrong with it."""


@dataclass(frozen=True)
class Verdict:
    """What the model decided.

    Attributes:
        fix: The action it chose, which is what gets scored.
        where: What to act on, in its own words.
        because: Its one-sentence reason.
        cited: The source ids it leaned on, each one it was actually given.
        overruled: Why a check replaced the model's choice, or empty when none did.
    """

    fix: Fix
    where: str
    because: str
    cited: list[str] = field(default_factory=list)
    overruled: str = ""


def _json_object(reply: str) -> dict[str, object]:
    """Read the JSON object out of a reply, allowing for text around it.

    Args:
        reply: What the model returned.

    Returns:
        The object.

    Raises:
        MalformedAnswerError: If there is no JSON object in the reply, or it isn't valid JSON.
    """
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < start:
        raise MalformedAnswerError(f"no JSON object in the reply: {reply[:200]!r}")
    try:
        parsed = json.loads(reply[start : end + 1])
    except json.JSONDecodeError as error:
        raise MalformedAnswerError(f"the reply isn't valid JSON: {error}") from error
    if not isinstance(parsed, dict):  # pragma: no cover - only a {...} span is extracted
        raise MalformedAnswerError(f"the reply is {type(parsed).__name__}, not an object")
    return parsed


def _chosen_fix(value: object) -> Fix:
    """Read the action the model chose, by key or by its text.

    Args:
        value: Whatever the reply held under 'fix'.

    Returns:
        The action.

    Raises:
        MalformedAnswerError: If it isn't one of the actions offered.
    """
    text = str(value).strip()
    by_name = {fix.name.casefold(): fix for fix in Fix}
    by_value = {fix.value.casefold(): fix for fix in Fix}
    chosen = by_name.get(text.casefold()) or by_value.get(text.casefold())
    if chosen is None:
        raise MalformedAnswerError(
            f"{text!r} isn't one of the actions offered: {', '.join(fix.name for fix in Fix)}"
        )
    return chosen


def parse_verdict(reply: str, source_ids: Sequence[str] = ()) -> Verdict:
    """Read a reply into a verdict, refusing anything unusable.

    Args:
        reply: What the model returned.
        source_ids: The sources it was given; citing anything else is an invention.

    Returns:
        The verdict.

    Raises:
        MalformedAnswerError: If the reply isn't a JSON object, names an action that wasn't
            offered, or cites a source that was never supplied.
    """
    parsed = _json_object(reply)
    cited = parsed.get("cited") or []
    if not isinstance(cited, list):
        raise MalformedAnswerError(f"'cited' is {type(cited).__name__}, not a list")
    citations = [str(item) for item in cited]
    invented = [citation for citation in citations if citation not in source_ids]
    if invented:
        raise MalformedAnswerError(f"cites sources it wasn't given: {invented}")
    return Verdict(
        fix=_chosen_fix(parsed.get("fix", "")),
        where=str(parsed.get("where", "")).strip(),
        because=str(parsed.get("because", "")).strip(),
        cited=citations,
    )
