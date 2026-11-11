"""Tests for the analyzer's alignment state."""

import pytest

from simulators.common.admin import AdminCommands
from simulators.common.faults import ALIGNMENT_ERROR, FaultSwitches
from simulators.spectrum_analyzer.alignment import CurrentAlignment, SwitchedAlignment


def test_a_current_alignment_has_no_error() -> None:
    """The default alignment never shifts a reading."""
    assert CurrentAlignment().reading_error_db() == 0.0


@pytest.mark.parametrize("error_db", [0.0, 0.5, 10.0])
def test_a_switched_alignment_reads_the_fault_switch(error_db: float) -> None:
    """The error is whatever the admin port's ALIGNMENT_ERROR_DB switch holds."""
    switches = FaultSwitches([ALIGNMENT_ERROR])
    commands = AdminCommands()
    switches.register_commands(commands)
    commands.handle(f"FAULT {ALIGNMENT_ERROR.name} {error_db:g}")
    assert SwitchedAlignment(switches).reading_error_db() == error_db
