"""Tests for SCPI command patterns and message parsing."""

import pytest

from simulators.common.errors import ScpiCommandError, ScpiErrorCode
from simulators.common.parser import (
    CommandPattern,
    expect_no_parameters,
    expect_one_parameter,
    parse_command,
    split_message,
)

FREQUENCY = CommandPattern.parse("SOURce:FREQuency[:CW]")


@pytest.mark.parametrize(
    "header", ["SOUR:FREQ", "sour:freq", "SOURce:FREQuency", ":SOUR:FREQ:CW", "SOURCE:FREQUENCY:CW"]
)
def test_short_and_long_forms_match(header: str) -> None:
    """Short forms, long forms, any case, a leading colon and the optional node all match."""
    assert FREQUENCY.matches(header)


@pytest.mark.parametrize("header", ["SOUR:FREQU", "FREQ", "SOUR:FREQ:CW:CW", "SOUR:POW", "SOUR"])
def test_other_headers_do_not_match(header: str) -> None:
    """Partial forms, missing nodes, extra nodes and other commands don't match."""
    assert not FREQUENCY.matches(header)


def test_optional_first_node() -> None:
    """A bracketed first node can be left out."""
    pattern = CommandPattern.parse("[SOURce]:POWer")
    assert pattern.matches("POW")
    assert pattern.matches("SOUR:POW")


def test_common_command_pattern() -> None:
    """Star commands match case-insensitively and need their star."""
    pattern = CommandPattern.parse("*IDN")
    assert pattern.matches("*idn")
    assert not pattern.matches("IDN")


@pytest.mark.parametrize("text", ["", "SOURce::FREQ", "[SOURce:FREQ", "SOUR ce", "SOUR[ce]"])
def test_malformed_patterns_are_rejected(text: str) -> None:
    """A badly written pattern is a programming error, caught at registration."""
    with pytest.raises(ValueError, match="pattern"):
        CommandPattern.parse(text)


def test_command_with_parameter() -> None:
    """A setting command splits into its header and its parameter."""
    command = parse_command("SOUR:FREQ 1 GHz")
    assert command.header == "SOUR:FREQ"
    assert not command.is_query
    assert command.parameters == ("1 GHz",)


def test_query_without_parameters() -> None:
    """A trailing question mark makes a query, and isn't part of the header."""
    command = parse_command("SOUR:POW?")
    assert command.header == "SOUR:POW"
    assert command.is_query
    assert not command.parameters


def test_parameters_split_on_commas() -> None:
    """Several parameters are separated by commas, with spaces trimmed."""
    assert parse_command("CALC:MARK 1, 2").parameters == ("1", "2")


@pytest.mark.parametrize("text", ["SOUR::FREQ 1", ":", "?", "SOUR:FREQ 1,,2"])
def test_malformed_commands_are_syntax_errors(text: str) -> None:
    """Empty nodes, empty headers and empty parameters are -102 syntax errors."""
    with pytest.raises(ScpiCommandError) as raised:
        parse_command(text)
    assert raised.value.error.code == ScpiErrorCode.SYNTAX_ERROR


def test_message_splits_on_semicolons() -> None:
    """One line can carry several commands; empty ones are dropped."""
    assert split_message("*CLS;*OPC? ; ;") == ["*CLS", "*OPC?"]


def test_expect_no_parameters_rejects_extras() -> None:
    """A command that takes no parameters reports -108 when given one."""
    with pytest.raises(ScpiCommandError) as raised:
        expect_no_parameters(("5",))
    assert raised.value.error.code == ScpiErrorCode.PARAMETER_NOT_ALLOWED


def test_expect_one_parameter_returns_it() -> None:
    """A command that takes one parameter gets exactly that parameter."""
    assert expect_one_parameter(("-10 dBm",)) == "-10 dBm"


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [((), ScpiErrorCode.MISSING_PARAMETER), (("1", "2"), ScpiErrorCode.PARAMETER_NOT_ALLOWED)],
)
def test_expect_one_parameter_rejects_wrong_counts(
    parameters: tuple[str, ...], expected: ScpiErrorCode
) -> None:
    """No parameter is -109 missing parameter; two is -108 parameter not allowed."""
    with pytest.raises(ScpiCommandError) as raised:
        expect_one_parameter(parameters)
    assert raised.value.error.code == expected


@pytest.mark.parametrize("text", ["SOUR:POW\t-10", "SOUR:POW  -10", "SOUR:POW \t -10"])
def test_any_whitespace_separates_header_and_parameters(text: str) -> None:
    """Tabs and repeated spaces both separate the header from its parameters."""
    command = parse_command(text)
    assert (command.header, command.parameters) == ("SOUR:POW", ("-10",))


@pytest.mark.parametrize("text", ["", "   "])
def test_blank_command_is_a_syntax_error(text: str) -> None:
    """A command with no header at all is a -102 syntax error."""
    with pytest.raises(ScpiCommandError) as raised:
        parse_command(text)
    assert raised.value.error.code == ScpiErrorCode.SYNTAX_ERROR


def test_numeric_suffixes_are_refused_until_supported() -> None:
    """A pattern with a numeric suffix fails loudly instead of matching inconsistently."""
    with pytest.raises(ValueError, match="suffix"):
        CommandPattern.parse("CALCulate:MARKer1:X")


@pytest.mark.parametrize("text", ["sense:freq", "SENSe:freq"])
def test_patterns_need_a_capital_short_form(text: str) -> None:
    """Every node must start with capitals, which form its short form."""
    with pytest.raises(ValueError, match="short form"):
        CommandPattern.parse(text)
