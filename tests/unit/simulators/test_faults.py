"""Tests for fault switches: faults set over the admin port, and their effects."""

import logging

import pytest

from simulators.common.admin import AdminCommands
from simulators.common.faults import (
    EXTRA_CABLE_LOSS,
    LINK_DOWN,
    LINK_FAILS_EVERY,
    SLOW_RESPONSES,
    FaultSwitches,
)
from simulators.common.physics import SourceState
from simulators.spectrum_analyzer.link import NO_SIGNAL, CableWithFaults

TONE = SourceState(frequency_hz=1e9, level_dbm=-10.0, output_on=True)
CABLE_LOSS_DB = 1.5


class FixedLink:
    """A generator link that always reports the same state."""

    def __init__(self, state: SourceState) -> None:
        """Report the given state."""
        self.state = state

    def source_state(self) -> SourceState:
        """Return the fixed state."""
        return self.state


def switches_with_commands() -> tuple[FaultSwitches, AdminCommands]:
    """Return fault switches for every fault, with their admin commands registered."""
    switches = FaultSwitches([EXTRA_CABLE_LOSS, LINK_DOWN, LINK_FAILS_EVERY, SLOW_RESPONSES])
    commands = AdminCommands()
    switches.register_commands(commands)
    return switches, commands


def test_faults_start_off() -> None:
    """Every fault's value is 0 until it is switched on."""
    switches, commands = switches_with_commands()
    assert switches.value(EXTRA_CABLE_LOSS.name) == 0.0
    assert commands.handle("FAULTS?") == {
        "active": {},
        "available": [
            "EXTRA_CABLE_LOSS_DB",
            "LINK_DOWN",
            "LINK_FAILS_EVERY",
            "SLOW_RESPONSE_S",
        ],
    }


def test_a_fault_is_switched_on_and_off_by_name_in_any_case() -> None:
    """FAULT sets a value, FAULTS? lists it, and FAULT ... OFF switches it off again."""
    switches, commands = switches_with_commands()
    assert commands.handle(f"fault extra_cable_loss_db {CABLE_LOSS_DB}") == {
        "fault": "EXTRA_CABLE_LOSS_DB",
        "value": CABLE_LOSS_DB,
    }
    assert switches.value("EXTRA_CABLE_LOSS_DB") == CABLE_LOSS_DB
    assert commands.handle("FAULTS?")["active"] == {"EXTRA_CABLE_LOSS_DB": CABLE_LOSS_DB}
    commands.handle("FAULT EXTRA_CABLE_LOSS_DB OFF")
    assert switches.value("EXTRA_CABLE_LOSS_DB") == 0.0
    assert commands.handle("FAULTS?")["active"] == {}


def test_clear_switches_every_fault_off() -> None:
    """CLEAR turns every fault off and says which ones were on."""
    switches, commands = switches_with_commands()
    commands.handle("FAULT EXTRA_CABLE_LOSS_DB 1")
    commands.handle("FAULT SLOW_RESPONSE_S 2.5")
    assert commands.handle("CLEAR") == {"cleared": ["EXTRA_CABLE_LOSS_DB", "SLOW_RESPONSE_S"]}
    assert switches.value("SLOW_RESPONSE_S") == 0.0


@pytest.mark.parametrize(
    ("line", "error"),
    [
        ("FAULT", "FAULT needs a fault name and a value, or OFF"),
        ("FAULT EXTRA_CABLE_LOSS_DB", "FAULT needs a fault name and a value, or OFF"),
        ("FAULT EXTRA_CABLE_LOSS_DB 1 2", "FAULT needs a fault name and a value, or OFF"),
        (
            "FAULT BROKEN_CABLE 1",
            "Unknown fault BROKEN_CABLE; available: EXTRA_CABLE_LOSS_DB, LINK_DOWN, "
            "LINK_FAILS_EVERY, SLOW_RESPONSE_S",
        ),
        ("FAULT EXTRA_CABLE_LOSS_DB lots", "EXTRA_CABLE_LOSS_DB needs a number in dB, got 'lots'"),
        ("FAULT EXTRA_CABLE_LOSS_DB nan", "EXTRA_CABLE_LOSS_DB needs a number in dB, got 'nan'"),
        ("FAULT EXTRA_CABLE_LOSS_DB -1", "EXTRA_CABLE_LOSS_DB must be from 0 to 40 dB, got -1"),
        ("FAULT SLOW_RESPONSE_S 31", "SLOW_RESPONSE_S must be from 0 to 30 s, got 31"),
        ("FAULTS? now", "FAULTS? takes no arguments"),
        ("CLEAR all", "CLEAR takes no arguments"),
    ],
)
def test_bad_fault_commands_are_refused_and_change_nothing(line: str, error: str) -> None:
    """Every malformed or out-of-range fault command gets a clear error and leaves faults off."""
    switches, commands = switches_with_commands()
    assert commands.handle(line) == {"error": error}
    assert switches.value("EXTRA_CABLE_LOSS_DB") == 0.0


def test_switching_a_fault_stays_out_of_info_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Fault changes are logged at debug level only, so logs never give the answer away."""
    caplog.set_level(logging.INFO)
    _, commands = switches_with_commands()
    commands.handle("FAULT EXTRA_CABLE_LOSS_DB 1")
    commands.handle("CLEAR")
    assert not caplog.records


def test_cable_loss_lowers_the_level_the_analyzer_receives() -> None:
    """With extra cable loss, the generator's level arrives that much lower."""
    switches, commands = switches_with_commands()
    cable = CableWithFaults(FixedLink(TONE), switches)
    assert cable.source_state() == TONE
    commands.handle("FAULT EXTRA_CABLE_LOSS_DB 1.25")
    assert cable.source_state() == SourceState(1e9, -11.25, output_on=True)


def test_a_disconnected_cable_leaves_the_analyzer_with_no_signal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With the cable down the analyzer sees nothing, and says so in its log."""
    switches, commands = switches_with_commands()
    cable = CableWithFaults(FixedLink(TONE), switches)
    commands.handle("FAULT LINK_DOWN 1")
    assert cable.source_state() == NO_SIGNAL
    assert any(
        "No signal reaching the analyzer" in record.getMessage() for record in caplog.records
    )


def test_a_loose_connector_drops_only_every_nth_reading() -> None:
    """Every third reading sees nothing; the others are normal, so failures look intermittent."""
    switches, commands = switches_with_commands()
    cable = CableWithFaults(FixedLink(TONE), switches)
    commands.handle("FAULT LINK_FAILS_EVERY 3")
    readings = [cable.source_state() for _ in range(6)]
    assert [reading == NO_SIGNAL for reading in readings] == [
        False,
        False,
        True,
        False,
        False,
        True,
    ]


def test_clearing_the_link_faults_restores_the_signal() -> None:
    """Once the faults are cleared, every reading sees the generator again."""
    switches, commands = switches_with_commands()
    cable = CableWithFaults(FixedLink(TONE), switches)
    commands.handle("FAULT LINK_DOWN 1")
    commands.handle("CLEAR")
    assert [cable.source_state() for _ in range(3)] == [TONE] * 3


@pytest.mark.parametrize("every", ["0.5", "0.9"])
def test_a_link_that_fails_less_than_every_sweep_leaves_it_alone(every: str) -> None:
    """A value below one sweep can't drop a reading, and must not divide by zero either."""
    switches, commands = switches_with_commands()
    cable = CableWithFaults(FixedLink(TONE), switches)
    commands.handle(f"FAULT LINK_FAILS_EVERY {every}")
    assert [cable.source_state() for _ in range(4)] == [TONE] * 4


def test_a_fractional_link_fault_counts_whole_sweeps() -> None:
    """2.5 sweeps means every second one: the value is truncated, never rounded up."""
    switches, commands = switches_with_commands()
    cable = CableWithFaults(FixedLink(TONE), switches)
    commands.handle("FAULT LINK_FAILS_EVERY 2.5")
    assert [cable.source_state() == NO_SIGNAL for _ in range(4)] == [False, True, False, True]
