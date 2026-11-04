"""Tests for how the RsInstrument session turns library errors into instrument errors."""

import pytest
from RsInstrument import TimeoutException

from drivers import session as session_module
from drivers.session import (
    InstrumentError,
    InstrumentTimeoutError,
    RsInstrumentSession,
    _arose_from_timeout,
)


class RaisingInstrument:
    """Stands in for RsInstrument: every query raises the error the test chose."""

    error: Exception = ValueError("unset")

    def __init__(self, *_arguments: object, **_options: object) -> None:
        """Accept RsInstrument's constructor arguments and connect to nothing."""

    def query_str(self, command: str) -> str:
        """Raise the chosen error.

        Args:
            command: The query.

        Raises:
            Exception: The error set on the class.
        """
        raise RaisingInstrument.error

    def write_str(self, command: str) -> None:
        """Accept a command."""

    def close(self) -> None:
        """Close nothing."""


@pytest.fixture(name="session")
def fixture_session(monkeypatch: pytest.MonkeyPatch) -> RsInstrumentSession:
    """Return a session whose library raises whatever RaisingInstrument.error holds."""
    monkeypatch.setattr(session_module, "RsInstrument", RaisingInstrument)
    return RsInstrumentSession("TCPIP::127.0.0.1::5025::SOCKET")


def value_error_during(context: BaseException) -> ValueError:
    """Return a ValueError raised while handling another error, as RsInstrument does."""
    error = ValueError("invalid status byte 1000000000")
    error.__context__ = context
    return error


def test_a_late_reply_after_a_timeout_is_a_timeout_and_the_session_stops(
    session: RsInstrumentSession,
) -> None:
    """A ValueError while handling a timeout is a timeout, and later commands are refused."""
    RaisingInstrument.error = value_error_during(TimeoutError("timed out"))
    with pytest.raises(InstrumentTimeoutError, match="late reply"):
        session.query("SOUR:FREQ?")
    with pytest.raises(InstrumentError, match="out of step after a timeout"):
        session.write("SOUR:POW -10")


def test_any_other_value_error_is_an_instrument_error_and_the_session_continues(
    session: RsInstrumentSession,
) -> None:
    """A ValueError with no timeout behind it fails the command, but the session stays usable."""
    RaisingInstrument.error = ValueError("unexpected reply")
    with pytest.raises(InstrumentError, match="unexpected reply") as raised:
        session.query("SOUR:FREQ?")
    assert not isinstance(raised.value, InstrumentTimeoutError)
    session.write("SOUR:POW -10")


def test_a_library_timeout_also_stops_the_session(session: RsInstrumentSession) -> None:
    """RsInstrument's own timeout marks the session out of step too."""
    RaisingInstrument.error = TimeoutException("Timeout expired")
    with pytest.raises(InstrumentTimeoutError):
        session.query("SOUR:FREQ?")
    with pytest.raises(InstrumentError, match="Open a new session"):
        session.query("SOUR:FREQ?")


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (TimeoutError("socket"), True),
        (TimeoutException("library"), True),
        (OSError("refused"), False),
    ],
)
def test_timeouts_are_found_in_the_context_chain(context: BaseException, expected: bool) -> None:
    """A timeout anywhere among an error's causes counts; other errors don't."""
    assert _arose_from_timeout(value_error_during(context)) is expected


def test_a_circular_context_chain_ends() -> None:
    """Errors whose context points back at themselves don't loop forever."""
    first, second = ValueError("first"), ValueError("second")
    first.__context__, second.__context__ = second, first
    assert _arose_from_timeout(first) is False
