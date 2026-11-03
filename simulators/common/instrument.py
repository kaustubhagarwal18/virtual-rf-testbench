"""The SCPI instrument base: common commands, the error queue and command dispatch.

Each simulated instrument subclasses ScpiInstrument and registers its own commands. The base
answers the IEEE 488.2 common commands and SYST:ERR?, which RsInstrument relies on.
"""

from collections.abc import Callable
from dataclasses import dataclass

from simulators.common.errors import ErrorQueue, ScpiCommandError, ScpiError, ScpiErrorCode
from simulators.common.numbers import parse_number
from simulators.common.parser import (
    CommandPattern,
    ParsedCommand,
    expect_no_parameters,
    expect_one_parameter,
    parse_command,
    split_message,
)
from simulators.common.status import EventStatusBit, StatusRegisters, event_bit_for

WriteHandler = Callable[[tuple[str, ...]], None]
"""Handles a setting command; receives the command's parameters."""

QueryHandler = Callable[[tuple[str, ...]], str]
"""Handles a query; receives the query's parameters and returns the reply."""


@dataclass(frozen=True)
class Identity:
    """What an instrument reports for *IDN?.

    Attributes:
        manufacturer: The maker's name.
        model: The model name.
        serial_number: The unit's serial number.
        firmware_version: The firmware version; for a simulator, its release version.
    """

    manufacturer: str
    model: str
    serial_number: str
    firmware_version: str

    def idn_reply(self) -> str:
        """Join the four fields as *IDN? reports them.

        Returns:
            Manufacturer, model, serial number and firmware version, separated by commas.
        """
        return f"{self.manufacturer},{self.model},{self.serial_number},{self.firmware_version}"


@dataclass(frozen=True)
class _Registration:
    """A command pattern with its handlers.

    Attributes:
        pattern: The command pattern.
        write: Handler for the setting form, if the command has one.
        query: Handler for the query form, if the command has one.
    """

    pattern: CommandPattern
    write: WriteHandler | None
    query: QueryHandler | None


def _register_value(parameters: tuple[str, ...]) -> int:
    """Read the single numeric parameter of *ESE or *SRE, rounded as IEEE 488.2 requires.

    Args:
        parameters: The parameters the command received.

    Returns:
        The value rounded to the nearest whole number.
    """
    return round(parse_number(expect_one_parameter(parameters)))


class ScpiInstrument:
    """An instrument that answers SCPI messages one line at a time.

    Commands registered later take precedence, so an instrument can replace a common command,
    for example an *OPC? that waits for a sweep to finish.
    """

    def __init__(self, identity: Identity, error_queue_capacity: int = 10) -> None:
        """Create the instrument with the common commands registered.

        Args:
            identity: What *IDN? reports.
            error_queue_capacity: How many errors the queue holds before it overflows.
        """
        self.identity = identity
        self._errors = ErrorQueue(error_queue_capacity)
        self._status = StatusRegisters()
        self._registrations: list[_Registration] = []
        self._register_common_commands()

    def register(
        self,
        pattern: str,
        *,
        write: WriteHandler | None = None,
        query: QueryHandler | None = None,
    ) -> None:
        """Add a command, written in SCPI manual notation such as 'SOURce:FREQuency[:CW]'.

        Args:
            pattern: The command pattern.
            write: Handler for the setting form, or None if the command can only be queried.
            query: Handler for the query form, or None if the command can't be queried.

        Raises:
            ValueError: If the command has neither handler. A malformed pattern also
                raises ValueError, from CommandPattern.parse.
        """
        if write is None and query is None:
            raise ValueError(f"Command pattern {pattern!r} needs a write or query handler")
        self._registrations.append(_Registration(CommandPattern.parse(pattern), write, query))

    def process(self, line: str) -> list[str]:
        """Run every command in one received line.

        Errors go into the error queue instead of being raised, as on a real instrument. A
        command error (-1xx) also discards the rest of the line, as instrument parsers do;
        an execution error lets the following commands run.

        Args:
            line: The received line, without its line ending.

        Returns:
            One reply per successful query, in order.
        """
        replies: list[str] = []
        for text in split_message(line):
            try:
                reply = self._execute(parse_command(text))
            except ScpiCommandError as raised:
                self._report(raised.error)
                if event_bit_for(raised.error.code) == EventStatusBit.COMMAND_ERROR:
                    break
            else:
                if reply is not None:
                    replies.append(reply)
        return replies

    def on_reset(self) -> None:
        """Restore the instrument's default settings; subclasses override this for *RST."""

    def _execute(self, command: ParsedCommand) -> str | None:
        """Run one parsed command through the latest registration that can handle it.

        Args:
            command: The command to run.

        Returns:
            The reply for a query, or None for a setting command.

        Raises:
            ScpiCommandError: -113 if no registration has a handler for this form.
        """
        for registration in reversed(self._registrations):
            if not registration.pattern.matches(command.header):
                continue
            if command.is_query and registration.query is not None:
                return registration.query(command.parameters)
            if not command.is_query and registration.write is not None:
                registration.write(command.parameters)
                return None
        raise ScpiCommandError(ScpiError(ScpiErrorCode.UNDEFINED_HEADER))

    def _report(self, error: ScpiError) -> None:
        """Queue an error and set its event status bit, plus the device error bit on overflow.

        Args:
            error: The error that occurred.
        """
        overflowed = self._errors.push(error)
        self._status.record_error(error.code)
        if overflowed:
            self._status.record_error(ScpiErrorCode.QUEUE_OVERFLOW)

    def _register_common_commands(self) -> None:
        """Register the IEEE 488.2 common commands and the error queue query."""
        self.register("*IDN", query=self._query_identity)
        self.register("*RST", write=self._reset)
        self.register("*CLS", write=self._clear_status)
        self.register("*OPC", write=self._operation_complete, query=self._query_complete)
        self.register("*ESE", write=self._set_event_enable, query=self._query_event_enable)
        self.register("*SRE", write=self._set_request_enable, query=self._query_request_enable)
        self.register("*ESR", query=self._query_event_status)
        self.register("*STB", query=self._query_status_byte)
        self.register("SYSTem:ERRor[:NEXT]", query=self._query_next_error)

    def _query_identity(self, parameters: tuple[str, ...]) -> str:
        """Answer *IDN?.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            The identity string.
        """
        expect_no_parameters(parameters)
        return self.identity.idn_reply()

    def _reset(self, parameters: tuple[str, ...]) -> None:
        """Handle *RST by letting the subclass restore its defaults.

        Args:
            parameters: The command's parameters; there must be none.
        """
        expect_no_parameters(parameters)
        self.on_reset()

    def _clear_status(self, parameters: tuple[str, ...]) -> None:
        """Handle *CLS: empty the error queue and the event status register.

        Args:
            parameters: The command's parameters; there must be none.
        """
        expect_no_parameters(parameters)
        self._errors.clear()
        self._status.clear()

    def _operation_complete(self, parameters: tuple[str, ...]) -> None:
        """Handle *OPC; commands finish immediately, so the bit is set at once.

        Args:
            parameters: The command's parameters; there must be none.
        """
        expect_no_parameters(parameters)
        self._status.set_operation_complete()

    def _query_complete(self, parameters: tuple[str, ...]) -> str:
        """Answer *OPC?; commands finish immediately, so the answer is always 1.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            Always '1'.
        """
        expect_no_parameters(parameters)
        return "1"

    def _set_event_enable(self, parameters: tuple[str, ...]) -> None:
        """Handle *ESE.

        Args:
            parameters: The command's parameters: one byte value.
        """
        self._status.set_event_status_enable(_register_value(parameters))

    def _query_event_enable(self, parameters: tuple[str, ...]) -> str:
        """Answer *ESE?.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            The event status enable mask.
        """
        expect_no_parameters(parameters)
        return str(self._status.event_status_enable)

    def _set_request_enable(self, parameters: tuple[str, ...]) -> None:
        """Handle *SRE.

        Args:
            parameters: The command's parameters: one byte value.
        """
        self._status.set_service_request_enable(_register_value(parameters))

    def _query_request_enable(self, parameters: tuple[str, ...]) -> str:
        """Answer *SRE?.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            The service request enable mask.
        """
        expect_no_parameters(parameters)
        return str(self._status.service_request_enable)

    def _query_event_status(self, parameters: tuple[str, ...]) -> str:
        """Answer *ESR?, which also clears the register.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            The event status register before it was cleared.
        """
        expect_no_parameters(parameters)
        return str(self._status.read_event_status())

    def _query_status_byte(self, parameters: tuple[str, ...]) -> str:
        """Answer *STB?.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            The status byte.
        """
        expect_no_parameters(parameters)
        return str(self._status.status_byte(errors_queued=len(self._errors) > 0))

    def _query_next_error(self, parameters: tuple[str, ...]) -> str:
        """Answer SYST:ERR? with the oldest queued error.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            The oldest error, formatted as code and quoted message.
        """
        expect_no_parameters(parameters)
        return self._errors.pop().format()
