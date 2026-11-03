"""Tests for reading SCPI numbers with units."""

import time

import pytest

from simulators.common.errors import ScpiCommandError, ScpiErrorCode
from simulators.common.numbers import FREQUENCY_UNITS, LEVEL_UNITS, parse_number


@pytest.mark.parametrize(
    "text", ["1 GHz", "1GHz", "+1 ghz", "1e9 Hz", "1000 MHz", "1000000 kHz", "1e9", "1.0E9"]
)
def test_frequency_forms_are_the_same_value(text: str) -> None:
    """Every way of writing 1 GHz reads as 1e9 Hz."""
    assert parse_number(text, FREQUENCY_UNITS) == pytest.approx(1e9)


@pytest.mark.parametrize(("text", "expected_dbm"), [("-10 dBm", -10.0), ("-10.5", -10.5)])
def test_level_reads_in_dbm(text: str, expected_dbm: float) -> None:
    """A level reads as dBm, with or without the unit."""
    assert parse_number(text, LEVEL_UNITS) == pytest.approx(expected_dbm)


@pytest.mark.parametrize("text", ["abc", "", "1..2", "nan", "inf", "1 2", "--1"])
def test_non_numbers_are_data_type_errors(text: str) -> None:
    """Text that isn't a number is a -104 data type error."""
    with pytest.raises(ScpiCommandError) as raised:
        parse_number(text, FREQUENCY_UNITS)
    assert raised.value.error.code == ScpiErrorCode.DATA_TYPE_ERROR


def test_unknown_unit_is_an_invalid_suffix() -> None:
    """A unit the setting doesn't accept is a -131 invalid suffix error."""
    with pytest.raises(ScpiCommandError) as raised:
        parse_number("10 V", LEVEL_UNITS)
    assert raised.value.error.code == ScpiErrorCode.INVALID_SUFFIX


def test_unit_on_a_unitless_value_is_an_invalid_suffix() -> None:
    """Any unit on a value that takes none is a -131 invalid suffix error."""
    with pytest.raises(ScpiCommandError) as raised:
        parse_number("10 Hz")
    assert raised.value.error.code == ScpiErrorCode.INVALID_SUFFIX


@pytest.mark.parametrize("text", ["1e999 GHz", "1e308 GHz", "-1e999"])
def test_values_too_large_for_a_float_are_out_of_range(text: str) -> None:
    """A value that overflows to infinity is a -222 data out of range error."""
    with pytest.raises(ScpiCommandError) as raised:
        parse_number(text, FREQUENCY_UNITS)
    assert raised.value.error.code == ScpiErrorCode.DATA_OUT_OF_RANGE


def test_long_malformed_input_is_rejected_quickly() -> None:
    """A long malformed parameter fails fast, so it can't freeze the instrument."""
    time_limit_s = 0.5
    started = time.perf_counter()
    with pytest.raises(ScpiCommandError) as raised:
        parse_number("1" * 10_000 + "!", FREQUENCY_UNITS)
    assert raised.value.error.code == ScpiErrorCode.DATA_TYPE_ERROR
    assert time.perf_counter() - started < time_limit_s
