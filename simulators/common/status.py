"""The IEEE 488.2 status registers that controllers such as RsInstrument read."""

from enum import IntFlag

from simulators.common.errors import ScpiCommandError, ScpiError, ScpiErrorCode

_BYTE_MAXIMUM = 255
_COMMAND_ERROR_CODES = range(-199, -99)
_EXECUTION_ERROR_CODES = range(-299, -199)
_DEVICE_ERROR_CODES = range(-399, -299)
_QUERY_ERROR_CODES = range(-499, -399)


class EventStatusBit(IntFlag):
    """Bits of the standard event status register, read with *ESR?."""

    OPERATION_COMPLETE = 1
    QUERY_ERROR = 4
    DEVICE_ERROR = 8
    EXECUTION_ERROR = 16
    COMMAND_ERROR = 32


class StatusByteBit(IntFlag):
    """Bits of the status byte, read with *STB?."""

    ERROR_QUEUE_NOT_EMPTY = 4
    EVENT_STATUS_SUMMARY = 32
    MASTER_SUMMARY = 64


_EVENT_BITS_BY_CLASS = (
    (_COMMAND_ERROR_CODES, EventStatusBit.COMMAND_ERROR),
    (_EXECUTION_ERROR_CODES, EventStatusBit.EXECUTION_ERROR),
    (_DEVICE_ERROR_CODES, EventStatusBit.DEVICE_ERROR),
    (_QUERY_ERROR_CODES, EventStatusBit.QUERY_ERROR),
)


def event_bit_for(code: int) -> int:
    """Return the event status bit an error sets, by its SCPI error class.

    Args:
        code: The error code.

    Returns:
        The bit for -1xx command, -2xx execution, -3xx device or -4xx query errors; 0 for
        'No error'; the device error bit for any instrument-specific code.
    """
    if code == ScpiErrorCode.NO_ERROR:
        return 0
    for codes, bit in _EVENT_BITS_BY_CLASS:
        if code in codes:
            return bit
    return EventStatusBit.DEVICE_ERROR


def _fits_in_byte(value: int) -> bool:
    """Check whether a value fits in an 8-bit register.

    Args:
        value: The value to check.

    Returns:
        True if the value is between 0 and 255.
    """
    return 0 <= value <= _BYTE_MAXIMUM


class StatusRegisters:
    """The event status register, its enable mask and the service request enable mask."""

    def __init__(self) -> None:
        """Start with no events and nothing enabled."""
        self._event_status = 0
        self._event_status_enable = 0
        self._service_request_enable = 0

    @property
    def event_status_enable(self) -> int:
        """The *ESE mask: which events show in the status byte.

        Returns:
            The mask, 0 to 255.
        """
        return self._event_status_enable

    @property
    def service_request_enable(self) -> int:
        """The *SRE mask: which status byte bits set the master summary bit.

        Returns:
            The mask, 0 to 255, with bit 6 always clear.
        """
        return self._service_request_enable

    def set_event_status_enable(self, value: int) -> None:
        """Set the *ESE mask.

        Args:
            value: The mask, 0 to 255.

        Raises:
            ScpiCommandError: -222 if the value doesn't fit in a byte.
        """
        if not _fits_in_byte(value):
            raise ScpiCommandError(ScpiError(ScpiErrorCode.DATA_OUT_OF_RANGE, str(value)))
        self._event_status_enable = value

    def set_service_request_enable(self, value: int) -> None:
        """Set the *SRE mask; bit 6 can't be enabled and is ignored.

        Args:
            value: The mask, 0 to 255.

        Raises:
            ScpiCommandError: -222 if the value doesn't fit in a byte.
        """
        if not _fits_in_byte(value):
            raise ScpiCommandError(ScpiError(ScpiErrorCode.DATA_OUT_OF_RANGE, str(value)))
        self._service_request_enable = value & ~int(StatusByteBit.MASTER_SUMMARY)

    def record_error(self, code: int) -> None:
        """Set the event bit for an error's class.

        Args:
            code: The error that occurred.
        """
        self._event_status |= event_bit_for(code)

    def set_operation_complete(self) -> None:
        """Set the operation complete bit, as *OPC does."""
        self._event_status |= EventStatusBit.OPERATION_COMPLETE

    def read_event_status(self) -> int:
        """Return the event status register and clear it, as *ESR? does.

        Returns:
            The register value before it was cleared.
        """
        value, self._event_status = self._event_status, 0
        return value

    def clear(self) -> None:
        """Clear the event status register, as *CLS does; the masks are kept."""
        self._event_status = 0

    def status_byte(self, *, errors_queued: bool) -> int:
        """Return the status byte, as *STB? reports it.

        Args:
            errors_queued: Whether the error queue holds unread errors.

        Returns:
            The status byte value.
        """
        value = 0
        if errors_queued:
            value |= StatusByteBit.ERROR_QUEUE_NOT_EMPTY
        if self._event_status & self._event_status_enable:
            value |= StatusByteBit.EVENT_STATUS_SUMMARY
        if value & self._service_request_enable:
            value |= StatusByteBit.MASTER_SUMMARY
        return value
