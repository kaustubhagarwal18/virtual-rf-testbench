"""Tests for parsing and refusing the model's answer."""

import json

import pytest

from triage.answer import MalformedAnswerError, parse_verdict
from triage.prompt import Fix


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
