"""Tests for parsing and refusing the model's answer."""

import json

import pytest

from triage.answer import (
    VERDICT_SCHEMA,
    MalformedAnswerError,
    PastFix,
    Verdict,
    grouped_past_fixes,
    parse_verdict,
    verdict_schema,
    with_past_fixes,
)
from triage.prompt import Fix, Source


@pytest.mark.parametrize("fix", list(Fix), ids=lambda fix: fix.name)
def test_an_action_named_by_its_text_is_accepted(fix: Fix) -> None:
    """The model may answer with any action's wording, not only its key."""
    assert parse_verdict(json.dumps({"fix": fix.value})).fix is fix


def test_text_around_the_json_is_tolerated() -> None:
    """A reply wrapped in prose still parses, since small models add it."""
    assert parse_verdict('here:\n{"fix": "LINK"}\nthanks').fix is Fix.LINK


def test_a_reply_with_no_object_is_refused() -> None:
    """A reply holding no braces at all can't be a verdict."""
    with pytest.raises(MalformedAnswerError, match="no JSON object"):
        parse_verdict("sorry, no idea")


def test_broken_json_is_refused() -> None:
    """A truncated object is reported as invalid JSON, not guessed at."""
    with pytest.raises(MalformedAnswerError, match="isn't valid JSON"):
        parse_verdict('{"fix": }')


def test_cited_that_isnt_a_list_is_refused() -> None:
    """'cited' must be a list of source ids."""
    with pytest.raises(MalformedAnswerError, match="'cited' is str, not a list"):
        parse_verdict('{"fix": "CABLE", "cited": "run-1"}', ["run-1"])


def test_an_action_not_on_the_list_is_refused() -> None:
    """A fix the model invented isn't one it was offered, so it is rejected."""
    with pytest.raises(MalformedAnswerError, match="isn't one of the actions offered"):
        parse_verdict('{"fix": "reboot everything"}')


@pytest.mark.parametrize("source_ids", [["past-1"], ["past-1", "past-2", "past-3"]])
def test_a_question_with_sources_may_cite_only_their_ids(source_ids: list[str]) -> None:
    """The narrowed schema lists the offered ids and changes nothing else."""
    schema = verdict_schema(source_ids)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert properties["cited"] == {
        "type": "array",
        "items": {"type": "string", "enum": source_ids},
    }
    general = VERDICT_SCHEMA["properties"]
    assert isinstance(general, dict)
    assert {k: v for k, v in properties.items() if k != "cited"} == {
        k: v for k, v in general.items() if k != "cited"
    }
    assert general["cited"] == {"type": "array", "items": {"type": "string"}}


def test_a_question_without_sources_keeps_the_general_schema() -> None:
    """With nothing to narrow to, the schema is the general one, so its prompts stay cached."""
    assert verdict_schema() is VERDICT_SCHEMA
    assert verdict_schema([]) is VERDICT_SCHEMA


def test_past_fixes_are_the_cited_sources_fixes_in_citation_order() -> None:
    """Only cited sources count, in the order cited; one whose fix wasn't recorded is skipped."""
    sources = [
        Source("past-a", "a", fix_applied="replaced the cable"),
        Source("past-b", "b", fix_applied="reseated the connector"),
        Source("past-c", "c"),
        Source("past-d", "d", fix_applied="never cited"),
    ]
    verdict = Verdict(Fix.CABLE, "cable", "loss", cited=["past-b", "past-c", "past-a"])
    attached = with_past_fixes(verdict, sources)
    assert attached.past_fixes == (
        PastFix("past-b", "reseated the connector"),
        PastFix("past-a", "replaced the cable"),
    )


CONFIG_SOURCES = [
    Source("past-a", "a", action="CONFIGURATION", fix_applied="Corrected level_offset_db"),
    Source("past-b", "b", action="CONFIGURATION", fix_applied="Set level_offset_db"),
    Source("past-c", "c", action="LINK", fix_applied="Reseated the link"),
    Source("past-d", "d", action="CONFIGURATION"),
]
"""Three closest past runs and one without a recorded fix, most similar first."""


def test_offered_runs_resolved_by_the_same_action_are_quoted_after_the_cited_ones() -> None:
    """A run the model didn't cite still shows its fix when its action is the verdict's."""
    verdict = Verdict(Fix.CONFIGURATION, "bench configuration", "offset", cited=["past-b"])
    assert with_past_fixes(verdict, CONFIG_SOURCES).past_fixes == (
        PastFix("past-b", "Set level_offset_db"),
        PastFix("past-a", "Corrected level_offset_db"),
    )


@pytest.mark.parametrize("fix", [Fix.UNKNOWN, Fix.CABLE])
def test_only_cited_runs_are_quoted_when_no_offered_run_shares_the_action(fix: Fix) -> None:
    """UNKNOWN, or an action no offered run was resolved by, quotes the cited runs alone."""
    verdict = Verdict(fix, "", "", cited=["past-c"])
    assert with_past_fixes(verdict, CONFIG_SOURCES).past_fixes == (
        PastFix("past-c", "Reseated the link"),
    )


def test_a_verdict_states_the_category_of_its_action() -> None:
    """The category is the action's: CABLE is the bench, UNKNOWN is unknown."""
    assert Verdict(Fix.CABLE, "cable", "loss").category == "bench"
    assert Verdict(Fix.UNKNOWN, "", "").category == "unknown"


def test_identical_past_fixes_are_grouped_under_every_run_that_recorded_them() -> None:
    """Two cited runs fixed the same way read as one fix with both ids, in first-cited order."""
    verdict = Verdict(
        Fix.CONFIGURATION,
        "bench configuration",
        "decimal point",
        past_fixes=(
            PastFix("past-a", "Corrected level_offset_db to 1.5"),
            PastFix("past-b", "Set level_offset_db"),
            PastFix("past-c", "Corrected level_offset_db to 1.5"),
        ),
    )
    assert grouped_past_fixes(verdict) == [
        ("Corrected level_offset_db to 1.5", ("past-a", "past-c")),
        ("Set level_offset_db", ("past-b",)),
    ]
