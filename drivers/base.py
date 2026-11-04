"""What every driver shares: identity, reset, waiting for completion, and reading replies."""

import math

from drivers.session import InstrumentError, ScpiSession
from drivers.types import InstrumentIdentity

_TRUE_REPLIES = frozenset({"1", "ON"})
_FALSE_REPLIES = frozenset({"0", "OFF"})
_IDN_FIELDS = 4


def scpi_number(value: float) -> str:
    """Format a number for a SCPI command, without needless digits.

    Args:
        value: The number.

    Returns:
        For example '2500000000' or '-10.5'.
    """
    return f"{value:.12g}"


def unexpected_reply(command: str, reply: str) -> str:
    """Describe a reply that can't be right.

    Args:
        command: The query that was sent.
        reply: What came back.

    Returns:
        The error message, naming both.
    """
    return f"Unexpected reply to {command!r}: {reply!r}"


class ScpiDriver:
    """The base of every driver: the IEEE 488.2 commands every instrument shares."""

    def __init__(self, session: ScpiSession) -> None:
        """Drive an instrument through a session.

        Args:
            session: The connection; any ScpiSession, real or fake.
        """
        self.session = session

    def identity(self) -> InstrumentIdentity:
        """Ask the instrument who it is.

        Returns:
            Manufacturer, model, serial number and firmware version.

        Raises:
            InstrumentError: If the *IDN? reply doesn't have exactly four fields.
        """
        reply = self.session.query("*IDN?")
        fields = [field.strip() for field in reply.split(",")]
        if len(fields) != _IDN_FIELDS:
            raise InstrumentError(unexpected_reply("*IDN?", reply))
        return InstrumentIdentity(*fields)

    def reset(self) -> None:
        """Reset the instrument to its defaults and wait until it has finished."""
        self.session.write("*RST")
        self.wait_until_complete()

    def wait_until_complete(self) -> None:
        """Wait until every command sent so far has finished, using *OPC?.

        Raises:
            InstrumentError: If *OPC? answers anything but 1.
        """
        reply = self.session.query("*OPC?")
        if reply.strip() != "1":
            raise InstrumentError(unexpected_reply("*OPC?", reply))

    def query_number(self, command: str) -> float:
        """Send a query whose reply is a number.

        Args:
            command: The SCPI query.

        Returns:
            The number.

        Raises:
            InstrumentError: If the reply isn't a finite number.
        """
        reply = self.session.query(command)
        try:
            value = float(reply)
        except ValueError as error:
            raise InstrumentError(unexpected_reply(command, reply)) from error
        if not math.isfinite(value):
            raise InstrumentError(unexpected_reply(command, reply))
        return value

    def query_bool(self, command: str) -> bool:
        """Send a query whose reply is ON/OFF or 1/0.

        Args:
            command: The SCPI query.

        Returns:
            True for ON or 1, False for OFF or 0.

        Raises:
            InstrumentError: For any other reply.
        """
        reply = self.session.query(command)
        word = reply.strip().upper()
        if word in _TRUE_REPLIES:
            return True
        if word in _FALSE_REPLIES:
            return False
        raise InstrumentError(unexpected_reply(command, reply))
