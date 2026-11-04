"""RsInstrumentSession's error translation, against a fake instrument that misbehaves on purpose."""

import pytest

from drivers.session import (
    InstrumentError,
    InstrumentTimeoutError,
    RsInstrumentSession,
    socket_resource,
)
from tests.integration.fake_instrument import Script, fake_instrument


def test_foreign_identity_is_refused() -> None:
    """An instrument that isn't VirtualRF is refused when the session opens (ADR 0001)."""
    with (
        fake_instrument(Script(idn="Acme,X-1,1,1.0")) as port,
        pytest.raises(InstrumentError, match="Can't open"),
    ):
        RsInstrumentSession(socket_resource("127.0.0.1", port), timeout_ms=500)


def test_silent_query_without_errors_times_out() -> None:
    """A query that gets no reply, with nothing in the error queue, is a plain timeout."""
    with (
        fake_instrument(Script(silent={"SOUR:POW?"})) as port,
        RsInstrumentSession(socket_resource("127.0.0.1", port), timeout_ms=300) as session,
        pytest.raises(InstrumentTimeoutError, match="no reply to 'SOUR:POW\\?'"),
    ):
        session.query("SOUR:POW?")


def test_dropped_connection_is_an_instrument_error() -> None:
    """If the instrument closes the connection, the command that finds it gone fails cleanly."""
    with (
        fake_instrument(Script()) as port,
        RsInstrumentSession(socket_resource("127.0.0.1", port), timeout_ms=300) as session,
        pytest.raises(InstrumentError, match="'DROP' failed"),
    ):
        session.write("DROP")
