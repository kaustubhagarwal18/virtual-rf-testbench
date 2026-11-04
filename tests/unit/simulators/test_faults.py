"""Tests for fault switches: faults set over the admin port, and their effects."""

import logging

import pytest

from simulators.common.admin import AdminCommands
from simulators.common.faults import (
    EXTRA_CABLE_LOSS,
    SLOW_RESPONSES,
    FaultSwitches,
)
from simulators.common.physics import SourceState
from simulators.spectrum_analyzer.link import CableWithFaults

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
    """Return fault switches for both faults, with their admin commands registered."""
    switches = FaultSwitches([EXTRA_CABLE_LOSS, SLOW_RESPONSES])
    commands = AdminCommands()
    switches.register_commands(commands)
    return switches, commands


def test_faults_start_off() -> None:
    """Every fault's value is 0 until it is switched on."""
    switches, commands = switches_with_commands()
    assert switches.value(EXTRA_CABLE_LOSS.name) == 0.0
    assert commands.handle("FAULTS?") == {
        "active": {},
        "available": ["EXTRA_CABLE_LOSS_DB", "SLOW_RESPONSE_S"],
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
            "Unknown fault BROKEN_CABLE; available: EXTRA_CABLE_LOSS_DB, SLOW_RESPONSE_S",
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
