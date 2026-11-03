"""Tests for the SCPI error codes and the instrument error queue."""

import pytest

from simulators.common.errors import ErrorQueue, ScpiError, ScpiErrorCode


def pop_codes(queue: ErrorQueue, count: int) -> list[ScpiErrorCode]:
    """Pop `count` entries from the queue and return their codes."""
    return [queue.pop().code for _ in range(count)]


def test_error_formats_as_code_and_quoted_message() -> None:
    """An error reads back as its code, a comma and the quoted standard message."""
    assert ScpiError(ScpiErrorCode.UNDEFINED_HEADER).format() == '-113,"Undefined header"'


def test_error_detail_follows_a_semicolon() -> None:
    """Instrument-specific detail goes after a semicolon inside the quotes."""
    error = ScpiError(ScpiErrorCode.DATA_OUT_OF_RANGE, "level above 20 dBm")
    assert error.format() == '-222,"Data out of range;level above 20 dBm"'


def test_empty_queue_reports_no_error() -> None:
    """Reading an empty queue gives the standard 'No error' entry."""
    assert ErrorQueue().pop().format() == '0,"No error"'


def test_queue_is_first_in_first_out() -> None:
    """Errors come out in the order they happened."""
    queue = ErrorQueue()
    queue.push(ScpiError(ScpiErrorCode.UNDEFINED_HEADER))
    queue.push(ScpiError(ScpiErrorCode.DATA_OUT_OF_RANGE))
    assert pop_codes(queue, 3) == [
        ScpiErrorCode.UNDEFINED_HEADER,
        ScpiErrorCode.DATA_OUT_OF_RANGE,
        ScpiErrorCode.NO_ERROR,
    ]


def test_overflow_keeps_oldest_errors_and_ends_with_queue_overflow() -> None:
    """A full queue keeps its oldest errors and replaces the newest with -350."""
    queue = ErrorQueue(capacity=3)
    for code in (
        ScpiErrorCode.SYNTAX_ERROR,
        ScpiErrorCode.UNDEFINED_HEADER,
        ScpiErrorCode.DATA_TYPE_ERROR,
        ScpiErrorCode.DATA_OUT_OF_RANGE,
    ):
        queue.push(ScpiError(code))
    assert pop_codes(queue, 3) == [
        ScpiErrorCode.SYNTAX_ERROR,
        ScpiErrorCode.UNDEFINED_HEADER,
        ScpiErrorCode.QUEUE_OVERFLOW,
    ]
    assert len(queue) == 0


def test_length_counts_queued_errors() -> None:
    """The queue length is the number of unread errors."""
    queue = ErrorQueue()
    queue.push(ScpiError(ScpiErrorCode.SYNTAX_ERROR))
    assert len(queue) == 1


def test_clear_empties_the_queue() -> None:
    """Clearing the queue discards every unread error."""
    queue = ErrorQueue()
    queue.push(ScpiError(ScpiErrorCode.SYNTAX_ERROR))
    queue.clear()
    assert len(queue) == 0


def test_capacity_below_two_is_rejected() -> None:
    """A queue needs room for one error plus the overflow marker."""
    with pytest.raises(ValueError, match="capacity"):
        ErrorQueue(capacity=1)


def test_quotes_in_detail_are_doubled() -> None:
    """Double quotes in the detail are doubled, so the reply stays one valid SCPI string."""
    error = ScpiError(ScpiErrorCode.SYNTAX_ERROR, 'BAD "x",')
    assert error.format() == '-102,"Syntax error;BAD ""x"","'


def test_push_reports_overflow() -> None:
    """Pushing into a full queue says that the queue overflowed."""
    queue = ErrorQueue(capacity=2)
    assert not queue.push(ScpiError(ScpiErrorCode.SYNTAX_ERROR))
    assert not queue.push(ScpiError(ScpiErrorCode.SYNTAX_ERROR))
    assert queue.push(ScpiError(ScpiErrorCode.SYNTAX_ERROR))
