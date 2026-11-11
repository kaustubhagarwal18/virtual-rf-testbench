"""The analyzer's self-alignment: whether its level readings carry an error, and how much.

A real analyzer aligns itself against an internal reference; when its temperature drifts
far from the last alignment, every level it reads is off by the same amount and it warns
until the alignment is run again. Here the error comes from a fault switch on the admin
port, so the instrument itself never reads fault switches.
"""

from typing import Protocol

from simulators.common.faults import ALIGNMENT_ERROR, FaultSwitches

ALIGNMENT_WARNING = (
    "Alignment data out of date: the internal temperature has changed since the last self-alignment"
)
"""What the analyzer logs on every sweep while its readings carry an alignment error."""


class Alignment(Protocol):
    """How far the analyzer's readings are off because its self-alignment is out of date."""

    def reading_error_db(self) -> float:
        """Return the error every level reading carries.

        Returns:
            The error in dB; 0 when the alignment is current.
        """


class CurrentAlignment:
    """An analyzer whose self-alignment is current: its readings carry no error."""

    def reading_error_db(self) -> float:
        """Return no error.

        Returns:
            0.0.
        """
        return 0.0


class SwitchedAlignment:
    """An alignment whose error is the ALIGNMENT_ERROR_DB fault switch's value."""

    def __init__(self, switches: FaultSwitches) -> None:
        """Read the error from these switches.

        Args:
            switches: The analyzer's fault switches, which must include ALIGNMENT_ERROR.
        """
        self._switches = switches

    def reading_error_db(self) -> float:
        """Return the switch's value.

        Returns:
            The error in dB; 0 while the fault is off.
        """
        return self._switches.value(ALIGNMENT_ERROR.name)
