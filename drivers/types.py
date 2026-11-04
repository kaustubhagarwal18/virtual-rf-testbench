"""Plain data the drivers return."""

from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentIdentity:
    """What an instrument reports for *IDN?.

    Attributes:
        manufacturer: The maker's name.
        model: The model name.
        serial_number: The unit's serial number.
        firmware_version: The firmware version; what tells a firmware regression apart.
    """

    manufacturer: str
    model: str
    serial_number: str
    firmware_version: str


@dataclass(frozen=True)
class PeakReading:
    """Where the analyzer's peak marker landed.

    Attributes:
        frequency_hz: The marker frequency.
        level_dbm: The marker level.
    """

    frequency_hz: float
    level_dbm: float
