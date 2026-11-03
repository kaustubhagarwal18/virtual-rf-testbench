"""Tests for the IEEE 488.2 status registers."""

import pytest

from simulators.common.errors import ScpiCommandError, ScpiErrorCode
from simulators.common.status import (
    EventStatusBit,
    StatusByteBit,
    StatusRegisters,
    event_bit_for,
)


def test_status_byte_is_zero_when_idle() -> None:
    """With no errors and no events, the status byte is 0."""
    assert StatusRegisters().status_byte(errors_queued=False) == 0


def test_queued_error_sets_bit_two() -> None:
    """Bit 2 of the status byte says the error queue isn't empty."""
    assert StatusRegisters().status_byte(errors_queued=True) == StatusByteBit.ERROR_QUEUE_NOT_EMPTY


@pytest.mark.parametrize(
    ("code", "expected_bit"),
    [
        (ScpiErrorCode.UNDEFINED_HEADER, EventStatusBit.COMMAND_ERROR),
        (ScpiErrorCode.DATA_OUT_OF_RANGE, EventStatusBit.EXECUTION_ERROR),
        (ScpiErrorCode.QUEUE_OVERFLOW, EventStatusBit.DEVICE_ERROR),
    ],
)
def test_error_sets_its_event_bit(code: ScpiErrorCode, expected_bit: EventStatusBit) -> None:
    """-1xx errors are command errors, -2xx execution errors and -3xx device errors."""
    status = StatusRegisters()
    status.record_error(code)
    assert status.read_event_status() == expected_bit


def test_reading_event_status_clears_it() -> None:
    """The event status register is cleared when it is read."""
    status = StatusRegisters()
    status.record_error(ScpiErrorCode.UNDEFINED_HEADER)
    status.read_event_status()
    assert status.read_event_status() == 0


def test_operation_complete_sets_bit_zero() -> None:
    """*OPC sets the operation complete bit."""
    status = StatusRegisters()
    status.set_operation_complete()
    assert status.read_event_status() == EventStatusBit.OPERATION_COMPLETE


def test_event_summary_needs_its_enable_bit() -> None:
    """An event shows in the status byte only once *ESE enables it."""
    status = StatusRegisters()
    status.record_error(ScpiErrorCode.UNDEFINED_HEADER)
    assert status.status_byte(errors_queued=False) == 0
    status.set_event_status_enable(EventStatusBit.COMMAND_ERROR)
    assert status.status_byte(errors_queued=False) == StatusByteBit.EVENT_STATUS_SUMMARY


def test_service_request_summary_follows_enable() -> None:
    """Bit 6 is set when an enabled status byte bit is set."""
    status = StatusRegisters()
    status.set_event_status_enable(EventStatusBit.COMMAND_ERROR)
    status.set_service_request_enable(StatusByteBit.EVENT_STATUS_SUMMARY)
    status.record_error(ScpiErrorCode.UNDEFINED_HEADER)
    assert status.status_byte(errors_queued=False) == (
        StatusByteBit.EVENT_STATUS_SUMMARY | StatusByteBit.MASTER_SUMMARY
    )


def test_enables_read_back() -> None:
    """*ESE? and *SRE? return the values that were set."""
    status = StatusRegisters()
    status.set_event_status_enable(32)
    status.set_service_request_enable(4)
    assert (status.event_status_enable, status.service_request_enable) == (32, 4)


def test_clear_resets_event_status_but_keeps_enables() -> None:
    """*CLS clears events; the enable registers stay as they were."""
    status = StatusRegisters()
    status.set_event_status_enable(EventStatusBit.COMMAND_ERROR)
    status.record_error(ScpiErrorCode.UNDEFINED_HEADER)
    status.clear()
    assert status.read_event_status() == 0
    assert status.event_status_enable == EventStatusBit.COMMAND_ERROR


@pytest.mark.parametrize("value", [-1, 256])
def test_enable_values_must_fit_in_a_byte(value: int) -> None:
    """Enable registers take 0 to 255; anything else is -222 data out of range."""
    with pytest.raises(ScpiCommandError) as raised:
        StatusRegisters().set_event_status_enable(value)
    assert raised.value.error.code == ScpiErrorCode.DATA_OUT_OF_RANGE


@pytest.mark.parametrize(
    ("code", "expected_bit"),
    [
        (0, 0),
        (-113, EventStatusBit.COMMAND_ERROR),
        (-222, EventStatusBit.EXECUTION_ERROR),
        (-350, EventStatusBit.DEVICE_ERROR),
        (-410, EventStatusBit.QUERY_ERROR),
        (201, EventStatusBit.DEVICE_ERROR),
    ],
)
def test_each_error_class_has_its_event_bit(code: int, expected_bit: int) -> None:
    """Each SCPI error class has its own event bit; 'No error' sets none."""
    assert event_bit_for(code) == expected_bit


@pytest.mark.parametrize("value", [-1, 256])
def test_service_request_enable_must_fit_in_a_byte(value: int) -> None:
    """*SRE takes 0 to 255 too; anything else is -222 data out of range."""
    with pytest.raises(ScpiCommandError) as raised:
        StatusRegisters().set_service_request_enable(value)
    assert raised.value.error.code == ScpiErrorCode.DATA_OUT_OF_RANGE


def test_service_request_enable_drops_only_bit_six() -> None:
    """*SRE ignores bit 6 but keeps every other bit, including bit 7."""
    status = StatusRegisters()
    status.set_service_request_enable(255)
    assert status.service_request_enable == 255 - StatusByteBit.MASTER_SUMMARY
