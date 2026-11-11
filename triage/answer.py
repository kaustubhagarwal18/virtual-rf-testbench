"""The model's answer: its shape, how it is read, and what makes it unusable.

An answer is rejected rather than guessed at. A reply that doesn't parse, names an action
that isn't on the list, or cites a source it was never given is a failure of the run, not
something to interpret generously: interpreting it would flatter the result.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from triage.pack import DEFAULT_PACK, UNKNOWN, Action
from triage.prompt import Source

_VERDICT_PROPERTIES: dict[str, object] = {
    "fix": {"type": "string", "enum": list(DEFAULT_PACK.keys)},
    "where": {"type": "string"},
    "because": {"type": "string"},
    "cited": {"type": "array", "items": {"type": "string"}},
}
"""The answer's fields, apart so a schema for one question can narrow one of them."""

VERDICT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": _VERDICT_PROPERTIES,
    "required": ["fix", "where", "because", "cited"],
}
"""The answer's shape as a JSON schema, for a model that can be constrained to it.

The action is an enum of the keys offered, so a constrained reply can't name an action off
the list or leave it empty. parse_verdict still checks everything: the schema narrows what
the model can say, it doesn't replace reading it strictly.
"""


def verdict_schema(source_ids: Sequence[str] = ()) -> dict[str, object]:
    """Return the answer's schema, with 'cited' narrowed to the sources this question has.

    A model that is constrained to the offered ids can't paste a source's text where its id
    belongs, which is how a reply that chose the right action used to come back unusable.
    With no sources there is nothing to narrow to, and the general schema is returned
    unchanged; parse_verdict still refuses any citation then.

    Args:
        source_ids: The ids of the sources offered with this question.

    Returns:
        A schema whose 'cited' items must each be one of the ids, or VERDICT_SCHEMA itself.
    """
    if not source_ids:
        return VERDICT_SCHEMA
    cited = {"type": "array", "items": {"type": "string", "enum": list(source_ids)}}
    return {**VERDICT_SCHEMA, "properties": {**_VERDICT_PROPERTIES, "cited": cited}}


class MalformedAnswerError(Exception):
    """The model's reply can't be used; the message says what was wrong with it."""


@dataclass(frozen=True)
class PastFix:
    """What fixed a past run the verdict cites, as that run's label records it.

    Attributes:
        source_id: The cited run's neutral id.
        fix_applied: Its recorded fix, word for word.
    """

    source_id: str
    fix_applied: str


@dataclass(frozen=True)
class Verdict:
    """What the model decided.

    Attributes:
        fix: The action it chose, which is what gets scored.
        where: What to act on, in its own words.
        because: Its one-sentence reason.
        cited: The source ids it leaned on, each one it was actually given.
        overruled: Why a check replaced the model's choice, or empty when none did.
        gate_reason: Why UNKNOWN was answered from the precedents alone, without asking the
            model; empty when the model was asked.
        past_fixes: What fixed each cited past run, then each other offered run resolved by
            the same action, quoted from its record, never written by the model; empty
            until with_past_fixes fills it in.
    """

    fix: Action
    where: str
    because: str
    cited: list[str] = field(default_factory=list)
    overruled: str = ""
    gate_reason: str = ""
    past_fixes: tuple[PastFix, ...] = ()

    @property
    def category(self) -> str:
        """Say whether the chosen action answers the firmware, the bench or the test code.

        Returns:
            'firmware', 'bench', 'test', or 'unknown' for UNKNOWN.
        """
        return self.fix.category

    @classmethod
    def gated(cls, reason: str) -> "Verdict":
        """Return the UNKNOWN verdict a gate gives, before anything is asked.

        Args:
            reason: Why the gate found the precedents no basis for an answer.

        Returns:
            UNKNOWN, with the reason as both the explanation and the gate's reason.
        """
        return cls(fix=UNKNOWN, where="no precedent to follow", because=reason, gate_reason=reason)


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


def _chosen_fix(value: object) -> Action:
    """Read the action the model chose, by key or by its text.

    Args:
        value: Whatever the reply held under 'fix'.

    Returns:
        The action.

    Raises:
        MalformedAnswerError: If it isn't one of the actions offered.
    """
    text = str(value).strip()
    by_key = {action.key.casefold(): action for action in DEFAULT_PACK.actions}
    by_says = {action.says.casefold(): action for action in DEFAULT_PACK.actions}
    chosen = by_key.get(text.casefold()) or by_says.get(text.casefold())
    if chosen is None:
        raise MalformedAnswerError(
            f"{text!r} isn't one of the actions offered: {', '.join(DEFAULT_PACK.keys)}"
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


def with_past_fixes(verdict: Verdict, sources: Sequence[Source]) -> Verdict:
    """Attach what fixed the past runs behind the verdict, quoted from their records.

    First each cited run, in citation order; then every other offered run whose recorded
    action is the verdict's, most similar first. A small model often cites a look-alike run
    of a neighbouring fault with the same action, so its own fault's runs, offered but not
    cited, would otherwise go unquoted. The quote comes from the record, not the model, so
    it can't be misremembered. A source whose fix wasn't recorded is left out rather than
    shown as blank.

    Args:
        verdict: The verdict, whose citations are already checked against the sources.
        sources: The sources offered for this run, most similar first.

    Returns:
        The verdict with past_fixes attached.
    """
    fix_by_id = {source.source_id: source.fix_applied for source in sources}
    same_action = [
        source.source_id
        for source in sources
        if source.action == verdict.fix.key and source.source_id not in verdict.cited
    ]
    past_fixes = tuple(
        PastFix(source_id, fix_by_id[source_id])
        for source_id in [*verdict.cited, *same_action]
        if fix_by_id.get(source_id)
    )
    return replace(verdict, past_fixes=past_fixes)


def grouped_past_fixes(verdict: Verdict) -> list[tuple[str, tuple[str, ...]]]:
    """Group the quoted runs' fixes by their text, so one fix recorded twice reads once.

    Args:
        verdict: The verdict, with its past fixes attached.

    Returns:
        Each distinct fix with the ids of the runs that recorded it, in first-quoted order.
    """
    groups: dict[str, list[str]] = {}
    for past in verdict.past_fixes:
        groups.setdefault(past.fix_applied, []).append(past.source_id)
    return [(fix, tuple(source_ids)) for fix, source_ids in groups.items()]


def past_fix_lines(verdict: Verdict) -> list[str]:
    """Write each distinct past fix beside the quoted runs that recorded it.

    Args:
        verdict: The verdict, with its past fixes attached.

    Returns:
        Lines such as 'Corrected level_offset_db to 1.5 (past-a, past-c)', in first-quoted
        order, so each fix can be traced to the run it came from.
    """
    return [f"{fix} ({', '.join(source_ids)})" for fix, source_ids in grouped_past_fixes(verdict)]
