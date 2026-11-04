"""SCPI sessions: the small interface drivers use, and its RsInstrument implementation.

Drivers depend only on ScpiSession, so unit tests can pass in a fake (ADR 0001), and another
transport would be one more class (ADR 0003). RsInstrumentSession uses the ADR 0001 settings:
a plain socket, the VirtualRF identity accepted, and RsInstrument's error check after every
command left on.
"""

import contextlib
from collections.abc import Iterator
from types import TracebackType
from typing import Protocol, Self

from RsInstrument import RsInstrException, RsInstrument, StatusException, TimeoutException

DEFAULT_TIMEOUT_MS = 2000
"""How long a command, a query or *OPC? may take before it times out."""


class InstrumentError(Exception):
    """An instrument reported an error, replied unexpectedly, or couldn't be reached."""


class InstrumentTimeoutError(InstrumentError):
    """An instrument didn't answer within the session's timeout."""


class ScpiSession(Protocol):
    """What a driver needs from a connection: send a command, or ask a question."""

    def write(self, command: str) -> None:
        """Send a command that has no reply.

        Args:
            command: The SCPI command.
        """

    def query(self, command: str) -> str:
        """Send a query and return its reply.

        Args:
            command: The SCPI query.

        Returns:
            The reply, without its line ending.
        """


def socket_resource(host: str, port: int) -> str:
    """Return the VISA resource name for a raw-socket instrument.

    Args:
        host: The instrument's address.
        port: Its SCPI port.

    Returns:
        For example 'TCPIP::127.0.0.1::5025::SOCKET'.
    """
    return f"TCPIP::{host}::{port}::SOCKET"


class RsInstrumentSession:
    """A ScpiSession on R&S's RsInstrument library, with every error turned into InstrumentError."""

    def __init__(
        self, resource: str, timeout_ms: int = DEFAULT_TIMEOUT_MS, *, reset: bool = True
    ) -> None:
        """Open the session, check the instrument's identity, and reset it unless told not to.

        Args:
            resource: The VISA resource name, for example from socket_resource().
            timeout_ms: How long connecting, any command and *OPC? may take.
            reset: Whether to send *RST when opening, so every session starts the same way.

        Raises:
            InstrumentError: If the instrument can't be reached or isn't accepted.
        """
        self.resource = resource
        options = (
            "SelectVisa='socket', SupportedIdnPatterns='VirtualRF', "
            f"OpenTimeout={timeout_ms}, VisaTimeout={timeout_ms}, OpcTimeout={timeout_ms}"
        )
        self._out_of_step = False
        try:
            self._instrument = RsInstrument(resource, id_query=True, reset=reset, options=options)
        except OSError as error:
            raise InstrumentError(f"Can't connect to {resource}: {error}") from error
        except RsInstrException as error:
            raise InstrumentError(f"Can't open {resource}: {error}") from error

    def write(self, command: str) -> None:
        """Send a command; RsInstrument then checks the error queue.

        Args:
            command: The SCPI command.
        """
        with self._translated_errors(command):
            self._instrument.write_str(command)

    def query(self, command: str) -> str:
        """Send a query and return its reply; RsInstrument then checks the error queue.

        Args:
            command: The SCPI query.

        Returns:
            The reply.
        """
        with self._translated_errors(command):
            reply: str = self._instrument.query_str(command)
        return reply

    def close(self) -> None:
        """Close the connection."""
        self._instrument.close()

    def __enter__(self) -> Self:
        """Use the session in a with block.

        Returns:
            The session.
        """
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the session at the end of a with block.

        Args:
            exception_type: The type of the exception that ended the block, if any.
            exception: That exception, if any; it isn't suppressed.
            traceback: Its traceback, if any.
        """
        self.close()

    @contextlib.contextmanager
    def _translated_errors(self, command: str) -> Iterator[None]:
        """Turn RsInstrument's and the socket's errors into InstrumentError.

        The command runs inside the with block. After a timeout, a late reply may still be on
        its way, and the next query could read it as its own answer; so a session that timed
        out refuses every further command, and the caller opens a new one.

        Args:
            command: The command being sent, for the error message.

        Raises:
            InstrumentError: If the session already timed out, if the instrument reports an
                error, or if the connection fails.
            InstrumentTimeoutError: If the instrument doesn't answer in time, including when
                it also queued errors (such as -113 for an unknown query), and when
                RsInstrument's own error check after the timeout reads a late reply.
        """
        if self._out_of_step:
            raise InstrumentError(
                f"{self.resource}: the session is out of step after a timeout, so {command!r} "
                "wasn't sent; a late reply may still arrive. Open a new session."
            )
        try:
            yield
        except TimeoutException as error:
            self._out_of_step = True
            raise InstrumentTimeoutError(f"{self.resource}: no reply to {command!r}") from error
        except StatusException as error:
            # RsInstrument reads the error queue after a timeout and reports both at once;
            # first_exc says the timeout came first.
            if error.first_exc is not None and issubclass(error.first_exc, TimeoutException):
                self._out_of_step = True
                raise InstrumentTimeoutError(
                    f"{self.resource}: no reply to {command!r}; instrument errors: {error}"
                ) from error
            raise InstrumentError(f"{self.resource}: {command!r} failed: {error}") from error
        except (RsInstrException, OSError) as error:
            raise InstrumentError(f"{self.resource}: {command!r} failed: {error}") from error
        except ValueError as error:
            if not _arose_from_timeout(error):
                raise InstrumentError(f"{self.resource}: {command!r} failed: {error}") from error
            self._out_of_step = True
            raise InstrumentTimeoutError(
                f"{self.resource}: no reply to {command!r} in time; a late reply then "
                f"confused the error check: {error}"
            ) from error


def _arose_from_timeout(error: BaseException) -> bool:
    """Check whether an error was raised while a timeout was being handled.

    RsInstrument checks the status byte after a timeout; if the late reply arrives first, it
    reads that reply as the status byte and raises ValueError, with the timeout as context.

    Args:
        error: The error to check.

    Returns:
        True if a socket timeout or RsInstrument timeout is among its causes or context.
    """
    seen: set[int] = set()
    cause: BaseException | None = error
    while cause is not None and id(cause) not in seen:
        if isinstance(cause, (TimeoutError, TimeoutException)):
            return True
        seen.add(id(cause))
        cause = cause.__cause__ or cause.__context__
    return False
