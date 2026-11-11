"""Fault switches: bench problems switched on over the admin port, never over SCPI.

Each fault is a number, 0 meaning off: extra cable loss in dB, or a delay in seconds before
each SCPI line is handled. The fault tooling sets them with admin commands:

    FAULT EXTRA_CABLE_LOSS_DB 1.5    switch a fault on, or change its value
    FAULT EXTRA_CABLE_LOSS_DB OFF    switch it off
    FAULTS?                          list the active and available faults
    CLEAR                            switch every fault off

Faults stay on through *RST, as a damaged cable would. Switching one is logged at debug
level only: a real bench doesn't announce its faults, and the logs are triage evidence.
"""

import logging
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from simulators.common.admin import (
    AdminCommandError,
    AdminCommands,
    AdminReply,
    expect_no_arguments,
)

_LOGGER = logging.getLogger(__name__)

_FAULT_ARGUMENTS = 2
"""FAULT takes a fault name and a value."""


@dataclass(frozen=True)
class FaultDefinition:
    """A fault a simulator can be switched into.

    Attributes:
        name: The name used in admin commands, with its unit, for example
            'EXTRA_CABLE_LOSS_DB'.
        unit: The value's unit, for messages.
        valid_range: The lowest and highest value accepted; 0 means off.
    """

    name: str
    unit: str
    valid_range: tuple[float, float]


EXTRA_CABLE_LOSS = FaultDefinition("EXTRA_CABLE_LOSS_DB", "dB", (0.0, 40.0))
"""Loss added to the cable between generator and analyzer, as from a damaged cable."""

LINK_DOWN = FaultDefinition("LINK_DOWN", "", (0.0, 1.0))
"""1 disconnects the cable between the instruments: the analyzer then sees no signal."""

LINK_FAILS_EVERY = FaultDefinition("LINK_FAILS_EVERY", "sweeps", (0.0, 1000.0))
"""A loose connector: every Nth reading of the generator's state fails, and only that one.
Whole sweeps only: the value is truncated, so anything below 1 leaves the link alone."""

ALIGNMENT_ERROR = FaultDefinition("ALIGNMENT_ERROR_DB", "dB", (0.0, 10.0))
"""An analyzer whose self-alignment is out of date: every level it reads is this much high."""

SLOW_RESPONSES = FaultDefinition("SLOW_RESPONSE_S", "s", (0.0, 30.0))
"""A delay before each SCPI line is handled, as from an overloaded instrument. The server
waits before taking the instrument's lock, so the admin port stays responsive."""

SLOW_SWEEP = FaultDefinition("SLOW_SWEEP_S", "s", (0.0, 30.0))
"""A delay before each sweep the analyzer is told to start, as from a sweep that takes far
longer than its settings call for. Every other line answers at once."""

_SWEEP_START = re.compile(r"^:?INIT(IATE)?\d*(:IMM(EDIATE)?)?$", re.IGNORECASE)
"""INITiate[<n>][:IMMediate] in any spelling: the command that starts a sweep."""

LineDelay = Callable[[str], float]
"""Given a received SCPI line, how many seconds it waits before it is handled."""


class FaultSwitches:
    """The faults one simulator offers, and their current values."""

    def __init__(self, definitions: Sequence[FaultDefinition]) -> None:
        """Offer the given faults, all switched off.

        Args:
            definitions: The faults this simulator can be switched into.
        """
        self._definitions = {definition.name: definition for definition in definitions}
        self._values: dict[str, float] = {}

    def value(self, name: str) -> float:
        """Return a fault's current value.

        Args:
            name: The fault's name.

        Returns:
            Its value, or 0.0 when it is off.
        """
        return self._values.get(name, 0.0)

    def register_commands(self, commands: AdminCommands) -> None:
        """Add the FAULT, FAULTS? and CLEAR admin commands.

        Args:
            commands: The simulator's admin commands.
        """
        commands.register("FAULT", self._set)
        commands.register("FAULTS?", self._report)
        commands.register("CLEAR", self._clear)

    def _set(self, arguments: tuple[str, ...]) -> AdminReply:
        """Handle FAULT <name> <value|OFF>.

        Args:
            arguments: The fault's name and its value, or OFF.

        Returns:
            The fault and its new value.

        Raises:
            AdminCommandError: If the arguments are missing, the fault is unknown, or the
                value isn't a number in the fault's range.
        """
        if len(arguments) != _FAULT_ARGUMENTS:
            raise AdminCommandError("FAULT needs a fault name and a value, or OFF")
        definition = self._definition(arguments[0])
        value = 0.0 if arguments[1].upper() == "OFF" else _parse_value(definition, arguments[1])
        if value:
            self._values[definition.name] = value
        else:
            self._values.pop(definition.name, None)
        _LOGGER.debug("Fault set", extra={"fault": definition.name, "value": value})
        return {"fault": definition.name, "value": value}

    def _report(self, arguments: tuple[str, ...]) -> AdminReply:
        """Handle FAULTS?.

        Args:
            arguments: The words after FAULTS?; there must be none.

        Returns:
            The active faults with their values, and every available fault's name.
        """
        expect_no_arguments("FAULTS?", arguments)
        return {
            "active": dict(sorted(self._values.items())),
            "available": sorted(self._definitions),
        }

    def _clear(self, arguments: tuple[str, ...]) -> AdminReply:
        """Handle CLEAR.

        Args:
            arguments: The words after CLEAR; there must be none.

        Returns:
            The faults that were on.
        """
        expect_no_arguments("CLEAR", arguments)
        cleared = sorted(self._values)
        self._values.clear()
        _LOGGER.debug("Faults cleared", extra={"faults": cleared})
        return {"cleared": cleared}

    def _definition(self, name: str) -> FaultDefinition:
        """Find a fault by name, regardless of case.

        Args:
            name: The name as sent.

        Returns:
            The fault's definition.

        Raises:
            AdminCommandError: If this simulator has no such fault.
        """
        definition = self._definitions.get(name.upper())
        if definition is None:
            available = ", ".join(sorted(self._definitions))
            raise AdminCommandError(f"Unknown fault {name}; available: {available}")
        return definition


def _parse_value(definition: FaultDefinition, text: str) -> float:
    """Read a fault value and check it is a finite number in the fault's range.

    Args:
        definition: The fault.
        text: The value as sent.

    Returns:
        The value.

    Raises:
        AdminCommandError: If it isn't a finite number, or is outside the range.
    """
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise AdminCommandError(
            f"{definition.name} needs a number in {definition.unit}, got {text!r}"
        )
    low, high = definition.valid_range
    if not low <= value <= high:
        raise AdminCommandError(
            f"{definition.name} must be from {low:g} to {high:g} {definition.unit}, got {text}"
        )
    return value


def starts_a_sweep(line: str) -> bool:
    """Check whether a SCPI line starts a sweep, in any of its message units.

    Args:
        line: The received line; units are separated by ';'.

    Returns:
        True when one of its units is INITiate[<n>][:IMMediate] with no parameters.
    """
    return any(_SWEEP_START.match(unit.strip()) for unit in line.split(";"))


def every_line_delay(switches: FaultSwitches, definition: FaultDefinition) -> LineDelay:
    """Delay every line by a fault switch's value.

    Args:
        switches: The instrument's fault switches.
        definition: The switch that holds the delay in seconds.

    Returns:
        The delay for any line.
    """

    def delay(line: str) -> float:
        """Return the switch's value, whatever the line.

        Args:
            line: The received line, unused.

        Returns:
            The delay in seconds.
        """
        del line
        return switches.value(definition.name)

    return delay


def sweep_delay(switches: FaultSwitches) -> LineDelay:
    """Delay only the lines that start a sweep, by the SLOW_SWEEP_S switch's value.

    Args:
        switches: The analyzer's fault switches, which must include SLOW_SWEEP.

    Returns:
        The delay for a line: the switch's value for a sweep start, otherwise 0.
    """

    def delay(line: str) -> float:
        """Return the sweep delay for a sweep start, and none for anything else.

        Args:
            line: The received line.

        Returns:
            The delay in seconds.
        """
        return switches.value(SLOW_SWEEP.name) if starts_a_sweep(line) else 0.0

    return delay
