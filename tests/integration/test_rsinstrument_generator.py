"""RsInstrument, as the drivers will use it, against the running signal generator simulator."""

import json
import socket
from collections.abc import Iterator

import pytest
from RsInstrument import RsInstrument, StatusException

from tests.integration.conftest import RunningSimulator

SESSION_OPTIONS = "SelectVisa='socket', SupportedIdnPatterns='VirtualRF'"
"""The session settings from ADR 0001: plain socket, and our own IDN accepted."""


@pytest.fixture(name="instrument")
def fixture_instrument(running_generator: RunningSimulator) -> Iterator[RsInstrument]:
    """Open an RsInstrument session on the generator, with a reset, and close it afterwards."""
    session = RsInstrument(
        f"TCPIP::127.0.0.1::{running_generator.scpi_port}::SOCKET",
        id_query=True,
        reset=True,
        options=SESSION_OPTIONS,
    )
    session.visa_timeout = 2000
    try:
        yield session
    finally:
        session.close()


def admin_state(admin_port: int) -> dict[str, object]:
    """Ask the generator's admin port for its state.

    Args:
        admin_port: The generator's admin port.

    Returns:
        The decoded STATE? reply.
    """
    with socket.create_connection(("127.0.0.1", admin_port), timeout=2.0) as connection:
        connection.sendall(b"STATE?\n")
        reply = connection.makefile("r").readline()
    state: dict[str, object] = json.loads(reply)
    return state


def test_rsinstrument_identifies_the_generator(instrument: RsInstrument) -> None:
    """RsInstrument accepts the VirtualRF identity and reads model and firmware version from it."""
    assert instrument.idn_string == "VirtualRF,SG-1,100001,9.9.9"
    assert instrument.full_instrument_model_name == "SG-1"
    assert instrument.instrument_firmware_version == "9.9.9"


def test_rsinstrument_sets_and_reads_back(instrument: RsInstrument) -> None:
    """Frequency, level and RF output set through RsInstrument read back unchanged."""
    instrument.write_str("SOUR:FREQ 2.5 GHz")
    instrument.write_str("SOUR:POW -10.5 dBm")
    instrument.write_str("OUTP ON")
    assert instrument.query_float("SOUR:FREQ?") == pytest.approx(2.5e9)
    assert instrument.query_float("SOUR:POW?") == pytest.approx(-10.5)
    assert instrument.query_bool("OUTP?")


def test_rsinstrument_raises_on_an_out_of_range_value(instrument: RsInstrument) -> None:
    """A level above +20 dBm makes RsInstrument raise with the instrument's own error."""
    with pytest.raises(StatusException, match="-222"):
        instrument.write_str("SOUR:POW 25")
    assert instrument.query_float("SOUR:POW?") == pytest.approx(-30.0)


def test_admin_state_follows_rsinstrument_settings(
    instrument: RsInstrument, running_generator: RunningSimulator
) -> None:
    """The admin port reports the settings RsInstrument just made, as the analyzer will see them."""
    instrument.write_str("SOUR:FREQ 2 GHz")
    instrument.write_str("SOUR:POW -12")
    instrument.write_str("OUTP ON")
    assert admin_state(running_generator.admin_port) == {
        "frequency_hz": 2e9,
        "level_dbm": -12.0,
        "output_on": True,
    }


def test_compound_query_keeps_rsinstrument_in_step(instrument: RsInstrument) -> None:
    """A compound query returns one line, so the next query still gets its own answer."""
    assert instrument.query_str("SOUR:FREQ?;SOUR:POW?") == "1000000000;-30"
    assert instrument.query_float("SOUR:FREQ?") == pytest.approx(1e9)
