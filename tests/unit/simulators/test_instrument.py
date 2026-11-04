"""Tests for the SCPI instrument base: common commands and dispatch."""

import pytest

from simulators.common.errors import ScpiCommandError, ScpiError, ScpiErrorCode
from simulators.common.instrument import Identity, ScpiInstrument
from simulators.common.numbers import LEVEL_UNITS, parse_number
from simulators.common.parser import expect_one_parameter
from simulators.common.status import EventStatusBit

IDENTITY = Identity(
    manufacturer="VirtualRF", model="TEST-1", serial_number="0001", firmware_version="1.2.3"
)


class LevelInstrument(ScpiInstrument):
    """A test instrument with one setting and a reset counter."""

    def __init__(self) -> None:
        """Register a level setting next to the common commands."""
        super().__init__(IDENTITY)
        self.level_dbm = 0.0
        self.reset_count = 0
        self.register("[SOURce]:POWer", write=self._set_level, query=self._get_level)
        self.register("OUTPut:PROTection:CLEar", write=self._refuse)

    def on_reset(self) -> None:
        """Count resets so the test can see *RST arrive."""
        self.reset_count += 1

    def _set_level(self, parameters: tuple[str, ...]) -> None:
        """Store the level from the single parameter."""
        self.level_dbm = parse_number(expect_one_parameter(parameters), LEVEL_UNITS)

    def _get_level(self, parameters: tuple[str, ...]) -> str:
        """Return the stored level."""
        del parameters
        return f"{self.level_dbm:.1f}"

    def _refuse(self, parameters: tuple[str, ...]) -> None:
        """Always fail with an execution error."""
        del parameters
        raise ScpiCommandError(ScpiError(ScpiErrorCode.DATA_OUT_OF_RANGE, "refused"))


def drain_errors(instrument: ScpiInstrument) -> list[str]:
    """Read the error queue until it reports no error, and return what was in it."""
    errors: list[str] = []
    while (entry := instrument.process("SYST:ERR?")[0]) != '0,"No error"':
        errors.append(entry)
    return errors


def test_identity_query() -> None:
    """*IDN? returns manufacturer, model, serial number and firmware version."""
    assert LevelInstrument().process("*IDN?") == ["VirtualRF,TEST-1,0001,1.2.3"]


def test_compound_message_answers_each_query() -> None:
    """Commands separated by semicolons run in order."""
    assert LevelInstrument().process("*CLS;*OPC?") == ["1"]


def test_empty_message_does_nothing() -> None:
    """A blank line produces no reply and no error."""
    instrument = LevelInstrument()
    assert not instrument.process(" ; ")
    assert not drain_errors(instrument)


def test_unknown_command_queues_undefined_header() -> None:
    """An unknown command is silent and leaves -113 in the error queue."""
    instrument = LevelInstrument()
    assert not instrument.process("BOGus 1")
    assert drain_errors(instrument) == ['-113,"Undefined header"']


def test_unknown_query_gets_no_reply() -> None:
    """An unknown query gets no reply at all, as on a real instrument."""
    instrument = LevelInstrument()
    assert not instrument.process("BOGus?")
    assert drain_errors(instrument) == ['-113,"Undefined header"']


def test_query_on_a_write_only_command_is_undefined() -> None:
    """Asking for a value a command can't report is -113."""
    instrument = LevelInstrument()
    assert not instrument.process("OUTP:PROT:CLE?")
    assert drain_errors(instrument) == ['-113,"Undefined header"']


def test_status_byte_shows_queued_error_until_read() -> None:
    """*STB? has bit 2 set while an error waits, and clears once it is read."""
    instrument = LevelInstrument()
    instrument.process("BOGus")
    assert instrument.process("*STB?") == ["4"]
    drain_errors(instrument)
    assert instrument.process("*STB?") == ["0"]


def test_long_form_error_query() -> None:
    """SYSTem:ERRor:NEXT? is the long form of SYST:ERR?."""
    assert LevelInstrument().process("SYSTem:ERRor:NEXT?") == ['0,"No error"']


def test_clear_status_empties_queue_and_events() -> None:
    """*CLS clears the error queue and the event status register."""
    instrument = LevelInstrument()
    instrument.process("BOGus")
    instrument.process("*CLS")
    assert not drain_errors(instrument)
    assert instrument.process("*ESR?") == ["0"]


def test_event_status_reports_command_error() -> None:
    """*ESR? has bit 5 set after a command error."""
    instrument = LevelInstrument()
    instrument.process("BOGus")
    assert instrument.process("*ESR?") == ["32"]


def test_operation_complete_command() -> None:
    """*OPC sets bit 0 of the event status register."""
    assert LevelInstrument().process("*OPC;*ESR?") == ["1"]


@pytest.mark.parametrize("register", ["*ESE", "*SRE"])
def test_enable_registers_set_and_read_back(register: str) -> None:
    """*ESE and *SRE store a byte value that their queries return."""
    instrument = LevelInstrument()
    instrument.process(f"{register} 36")
    assert instrument.process(f"{register}?") == ["36"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("*ESE 300", '-222,"Data out of range;300"'),
        ("*ESE abc", '-104,"Data type error;abc"'),
        ("*ESE", '-109,"Missing parameter"'),
        ("*CLS 5", '-108,"Parameter not allowed"'),
        ("*IDN? 1", '-108,"Parameter not allowed"'),
        ("SOUR::POW 1", '-102,"Syntax error;SOUR::POW 1"'),
    ],
)
def test_bad_parameters_queue_the_right_error(command: str, expected: str) -> None:
    """Each kind of bad input leaves its standard error, with the offending text as detail."""
    instrument = LevelInstrument()
    instrument.process(command)
    assert drain_errors(instrument) == [expected]


def test_registered_command_reaches_its_handler() -> None:
    """A subclass's setting works in short form, long form and with units."""
    instrument = LevelInstrument()
    instrument.process("SOURce:POWer -10 dBm")
    assert instrument.process("POW?") == ["-10.0"]


def test_handler_error_is_queued_as_execution_error() -> None:
    """An error raised by a handler lands in the queue and sets the execution error bit."""
    instrument = LevelInstrument()
    instrument.process("OUTP:PROT:CLE")
    assert drain_errors(instrument) == ['-222,"Data out of range;refused"']
    assert instrument.process("*ESR?") == ["16"]


def test_reset_calls_the_instrument_reset_hook() -> None:
    """*RST reaches the subclass so it can restore its default settings."""
    instrument = LevelInstrument()
    instrument.process("*RST")
    assert instrument.reset_count == 1


def test_registration_without_handlers_is_rejected() -> None:
    """A command with neither a write nor a query handler is a programming error."""
    with pytest.raises(ValueError, match="handler"):
        LevelInstrument().register("OUTPut")


def test_error_queue_capacity_is_configurable() -> None:
    """An instrument can choose a smaller error queue than the default."""
    instrument = ScpiInstrument(IDENTITY, error_queue_capacity=2)
    for command in ("A", "B", "C"):
        instrument.process(command)
    assert drain_errors(instrument) == ['-113,"Undefined header"', '-350,"Queue overflow"']


def test_later_registration_overrides_a_common_command() -> None:
    """An instrument can replace a common command, such as *OPC? waiting for a sweep."""
    instrument = ScpiInstrument(IDENTITY)
    instrument.register("*OPC", query=lambda _parameters: "sweep done")
    assert instrument.process("*OPC?") == ["sweep done"]


def test_write_and_query_can_be_registered_separately() -> None:
    """Two registrations of one command, one per form, both work."""
    stored: list[str] = []
    instrument = ScpiInstrument(IDENTITY)
    instrument.register("FOO", write=stored.extend)
    instrument.register("FOO", query=lambda _parameters: "42")
    instrument.process("FOO 7")
    assert (instrument.process("FOO?"), stored) == (["42"], ["7"])


def test_tab_between_header_and_value() -> None:
    """A tab separates a command from its value, as SCPI allows."""
    instrument = LevelInstrument()
    instrument.process("*ESE\t36")
    assert instrument.process("*ESE?") == ["36"]


def test_queue_overflow_sets_the_device_error_bit() -> None:
    """Overflow sets the device error bit as well as the bit of the error that caused it."""
    instrument = ScpiInstrument(IDENTITY, error_queue_capacity=2)
    for command in ("A", "B", "C"):
        instrument.process(command)
    expected = EventStatusBit.COMMAND_ERROR | EventStatusBit.DEVICE_ERROR
    assert instrument.process("*ESR?") == [str(int(expected))]


@pytest.mark.parametrize(("sent", "stored"), [("32.4", "32"), ("4.6", "5"), ("1.0e1", "10")])
def test_enable_values_are_rounded(sent: str, stored: str) -> None:
    """*ESE and *SRE round decimal values to the nearest whole number, as IEEE 488.2 says."""
    instrument = LevelInstrument()
    instrument.process(f"*SRE {sent}")
    assert instrument.process("*SRE?") == [stored]


def test_command_error_discards_the_rest_of_the_line() -> None:
    """After a command error, the remaining commands on that line are ignored."""
    instrument = LevelInstrument()
    assert not instrument.process("BOGus;*OPC?")
    assert drain_errors(instrument) == ['-113,"Undefined header"']


def test_execution_error_lets_the_line_continue() -> None:
    """An execution error doesn't stop the commands after it."""
    instrument = LevelInstrument()
    assert instrument.process("OUTP:PROT:CLE;*OPC?") == ["1"]


def test_handler_bug_queues_device_error_and_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A bug in a handler queues -300, discards the rest of the line and logs the traceback."""
    instrument = ScpiInstrument(IDENTITY)

    def broken(_parameters: tuple[str, ...]) -> str:
        """Fail the way a programming error would."""
        raise RuntimeError("bug")

    instrument.register("BROKen", query=broken)
    assert not instrument.process("BROK?;*OPC?")
    assert drain_errors(instrument) == ['-300,"Device-specific error;internal error"']
    assert any(record.exc_info for record in caplog.records)


def test_rejected_command_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Each rejected command is logged with its text and error, for later triage."""
    LevelInstrument().process("BOGus 1")
    record = next(record for record in caplog.records if record.getMessage() == "Command rejected")
    assert (record.__dict__["command"], record.__dict__["error"]) == (
        "BOGus 1",
        '-113,"Undefined header"',
    )
