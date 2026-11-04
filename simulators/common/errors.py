"""SCPI error codes, the exception handlers raise, and the instrument error queue."""

from collections import deque
from dataclasses import dataclass
from enum import IntEnum


class ScpiErrorCode(IntEnum):
    """Standard SCPI error codes used by the simulators.

    The hundreds digit gives the class: -1xx command errors, -2xx execution errors,
    -3xx device-specific errors, -4xx query errors.
    """

    NO_ERROR = 0
    SYNTAX_ERROR = -102
    DATA_TYPE_ERROR = -104
    PARAMETER_NOT_ALLOWED = -108
    MISSING_PARAMETER = -109
    UNDEFINED_HEADER = -113
    INVALID_SUFFIX = -131
    SETTINGS_CONFLICT = -221
    DATA_OUT_OF_RANGE = -222
    ILLEGAL_PARAMETER_VALUE = -224
    DATA_CORRUPT_OR_STALE = -230
    DEVICE_SPECIFIC_ERROR = -300
    QUEUE_OVERFLOW = -350


_STANDARD_MESSAGES: dict[ScpiErrorCode, str] = {
    ScpiErrorCode.NO_ERROR: "No error",
    ScpiErrorCode.SYNTAX_ERROR: "Syntax error",
    ScpiErrorCode.DATA_TYPE_ERROR: "Data type error",
    ScpiErrorCode.PARAMETER_NOT_ALLOWED: "Parameter not allowed",
    ScpiErrorCode.MISSING_PARAMETER: "Missing parameter",
    ScpiErrorCode.UNDEFINED_HEADER: "Undefined header",
    ScpiErrorCode.INVALID_SUFFIX: "Invalid suffix",
    ScpiErrorCode.SETTINGS_CONFLICT: "Settings conflict",
    ScpiErrorCode.DATA_OUT_OF_RANGE: "Data out of range",
    ScpiErrorCode.ILLEGAL_PARAMETER_VALUE: "Illegal parameter value",
    ScpiErrorCode.DATA_CORRUPT_OR_STALE: "Data corrupt or stale",
    ScpiErrorCode.DEVICE_SPECIFIC_ERROR: "Device-specific error",
    ScpiErrorCode.QUEUE_OVERFLOW: "Queue overflow",
}


@dataclass(frozen=True)
class ScpiError:
    """One entry in an instrument's error queue.

    Attributes:
        code: The standard SCPI error code.
        detail: Optional instrument-specific text, shown after the standard message.
    """

    code: ScpiErrorCode
    detail: str = ""

    def format(self) -> str:
        """Return the entry as SYST:ERR? reports it.

        Double quotes inside the message are doubled, as SCPI strings require, so text
        copied from a bad command can't break the reply.

        Returns:
            The code and quoted message, for example '-113,"Undefined header"'.
        """
        message = _STANDARD_MESSAGES[self.code]
        if self.detail:
            message = f"{message};{self.detail}"
        escaped = message.replace('"', '""')
        return f'{int(self.code)},"{escaped}"'


class ScpiCommandError(Exception):
    """Raised while handling a command; the instrument queues the error it carries."""

    def __init__(self, error: ScpiError) -> None:
        """Wrap an error so it can travel up to the instrument's dispatcher.

        Args:
            error: The error to put in the instrument's error queue.
        """
        super().__init__(error.format())
        self.error = error


NO_ERROR = ScpiError(ScpiErrorCode.NO_ERROR)
_QUEUE_OVERFLOW = ScpiError(ScpiErrorCode.QUEUE_OVERFLOW)
_MINIMUM_CAPACITY = 2


class ErrorQueue:
    """A first-in, first-out error queue that reports overflow the SCPI way."""

    def __init__(self, capacity: int = 10) -> None:
        """Create an empty queue.

        Args:
            capacity: How many entries the queue holds, including the overflow marker.

        Raises:
            ValueError: If the capacity can't hold one error plus the overflow marker.
        """
        if capacity < _MINIMUM_CAPACITY:
            raise ValueError(f"Error queue capacity must be at least {_MINIMUM_CAPACITY}")
        self._capacity = capacity
        self._entries: deque[ScpiError] = deque()

    def __len__(self) -> int:
        """Count the unread errors.

        Returns:
            The number of errors waiting to be read.
        """
        return len(self._entries)

    def push(self, error: ScpiError) -> bool:
        """Add an error; when the queue is full, the newest entry becomes -350.

        Args:
            error: The error to record.

        Returns:
            True if the queue was full and overflowed, False if the error was stored.
        """
        if len(self._entries) < self._capacity:
            self._entries.append(error)
            return False
        self._entries[-1] = _QUEUE_OVERFLOW
        return True

    def pop(self) -> ScpiError:
        """Remove and return the oldest error.

        Returns:
            The oldest error, or the 'No error' entry when the queue is empty.
        """
        return self._entries.popleft() if self._entries else NO_ERROR

    def clear(self) -> None:
        """Discard every unread error."""
        self._entries.clear()
