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

        The command runs inside the with block.

        Args:
            command: The command being sent, for the error message.

        Raises:
            InstrumentTimeoutError: If the instrument doesn't answer in time, including when it
                also queued errors, such as -113 for an unknown query.
            InstrumentError: If it reports an error or the connection fails.
        """
        try:
            yield
        except TimeoutException as error:
            raise InstrumentTimeoutError(f"{self.resource}: no reply to {command!r}") from error
        except StatusException as error:
            # RsInstrument reads the error queue after a timeout and reports both at once;
            # first_exc says the timeout came first.
            if error.first_exc is not None and issubclass(error.first_exc, TimeoutException):
                raise InstrumentTimeoutError(
                    f"{self.resource}: no reply to {command!r}; instrument errors: {error}"
                ) from error
            raise InstrumentError(f"{self.resource}: {command!r} failed: {error}") from error
        except (RsInstrException, OSError) as error:
            raise InstrumentError(f"{self.resource}: {command!r} failed: {error}") from error
