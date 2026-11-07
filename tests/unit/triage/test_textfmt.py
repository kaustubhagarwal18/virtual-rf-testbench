"""Tests for the shared report text helpers."""

from triage.textfmt import clip


def test_short_text_is_returned_unchanged() -> None:
    """Text within the limit comes back as-is."""
    assert clip("one two three four", 100) == "one two three four"


def test_long_text_is_cut_to_whole_words_with_an_ellipsis() -> None:
    """Text over the limit is shortened at a word boundary, never mid-word."""
    assert clip("one two three four", 9) == "one two..."


def test_text_exactly_at_the_limit_is_not_clipped() -> None:
    """A value whose collapsed length equals the limit keeps its last word."""
    assert clip("one two", 7) == "one two"


def test_whitespace_including_newlines_is_collapsed() -> None:
    """A multi-line value becomes one line of single-spaced words."""
    assert clip("one\n  two\t three", 100) == "one two three"
