"""Unit tests for the signal generator and spectrum analyzer drivers, on a fake session."""

from collections.abc import Callable

import pytest

from drivers.protocols import LevelSource, PowerMeter
from drivers.session import InstrumentError
from drivers.signal_generator import SignalGenerator
from drivers.spectrum_analyzer import SpectrumAnalyzer
from drivers.types import InstrumentIdentity, PeakReading
from tests.unit.drivers.fakes import FakeSession

IDN = "VirtualRF,SG-1,100001,1.2.3"


def test_identity_is_parsed_into_its_four_fields() -> None:
    """identity() asks *IDN? and splits the reply into manufacturer, model, serial, version."""
    generator = SignalGenerator(FakeSession({"*IDN?": IDN}))
    assert generator.identity() == InstrumentIdentity("VirtualRF", "SG-1", "100001", "1.2.3")


def test_malformed_identity_is_an_instrument_error() -> None:
    """An *IDN? reply without four fields is reported, not guessed at."""
    generator = SignalGenerator(FakeSession({"*IDN?": "VirtualRF,SG-1"}))
    with pytest.raises(InstrumentError, match=r"\*IDN\?.*'VirtualRF,SG-1'"):
        generator.identity()


def test_reset_waits_for_completion() -> None:
    """reset() sends *RST and waits on *OPC?."""
    session = FakeSession()
    SignalGenerator(session).reset()
    assert session.sent == ["*RST", "*OPC?"]


def test_generator_configuration_is_one_command_group() -> None:
    """Frequency and level are set together, then the group waits on *OPC?."""
    session = FakeSession()
    SignalGenerator(session).configure(frequency_hz=2.5e9, level_dbm=-10.5)
    assert session.sent == ["SOUR:FREQ 2500000000", "SOUR:POW -10.5", "*OPC?"]


@pytest.mark.parametrize(("on", "command"), [(True, "OUTP ON"), (False, "OUTP OFF")])
def test_rf_output_switch_waits_for_completion(on: bool, command: str) -> None:
    """Switching RF waits on *OPC?, so the next measurement sees the new state."""
    session = FakeSession()
    SignalGenerator(session).set_output(on=on)
    assert session.sent == [command, "*OPC?"]


def test_generator_settings_read_back_as_numbers() -> None:
    """Frequency, level and RF state come back as numbers and a bool."""
    session = FakeSession({"SOUR:FREQ?": "1000000000", "SOUR:POW?": "-30", "OUTP?": "1"})
    generator = SignalGenerator(session)
    assert (generator.frequency_hz(), generator.level_dbm(), generator.output_on()) == (
        1e9,
        -30.0,
        True,
    )


@pytest.mark.parametrize(
    ("replies", "read"),
    [
        ({"SOUR:FREQ?": "fast"}, SignalGenerator.frequency_hz),
        ({"OUTP?": "maybe"}, SignalGenerator.output_on),
        ({"*OPC?": "0"}, SignalGenerator.reset),
    ],
)
def test_unexpected_replies_are_instrument_errors(
    replies: dict[str, str], read: Callable[[SignalGenerator], object]
) -> None:
    """A reply that can't be what the instrument should say is an error naming it."""
    generator = SignalGenerator(FakeSession(replies))
    with pytest.raises(InstrumentError, match="Unexpected reply"):
        read(generator)


def test_instrument_errors_reach_the_caller() -> None:
    """An error the instrument reports stops the command group instead of being swallowed."""
    error = InstrumentError('-222,"Data out of range;25 dBm"')
    session = FakeSession(failures={"SOUR:POW 25": error})
    with pytest.raises(InstrumentError, match="-222"):
        SignalGenerator(session).configure(frequency_hz=1e9, level_dbm=25.0)
    assert session.sent == ["SOUR:FREQ 1000000000", "SOUR:POW 25"]


def test_analyzer_configuration_selects_single_sweep() -> None:
    """Centre, span and RBW are set, with single sweep so readings come from a known sweep."""
    session = FakeSession()
    SpectrumAnalyzer(session).configure(centre_hz=1e9, span_hz=1e6, rbw_hz=1e3)
    assert session.sent == [
        "FREQ:CENT 1000000000",
        "FREQ:SPAN 1000000",
        "BAND 1000",
        "INIT:CONT OFF",
        "*OPC?",
    ]


def test_peak_measurement_sweeps_then_reads_the_marker() -> None:
    """A peak measurement takes a sweep, waits for it, searches the peak and reads the marker."""
    session = FakeSession({"CALC1:MARK1:X?": "1000000000", "CALC1:MARK1:Y?": "-11.55"})
    assert SpectrumAnalyzer(session).measure_peak() == PeakReading(
        frequency_hz=1e9, level_dbm=-11.55
    )
    assert session.sent == [
        "INIT",
        "*OPC?",
        "CALC1:MARK1:MAX",
        "CALC1:MARK1:X?",
        "CALC1:MARK1:Y?",
    ]


def test_channel_power_sets_the_channel_then_sweeps() -> None:
    """Channel power sets the channel width, takes a sweep and reads the result."""
    channel_power_dbm = -11.5
    session = FakeSession({"CALC1:MARK1:FUNC:POW:RES? CPOW": str(channel_power_dbm)})
    analyzer = SpectrumAnalyzer(session)
    assert analyzer.measure_channel_power_dbm(channel_bandwidth_hz=1e5) == channel_power_dbm
    assert session.sent == [
        "POW:ACH:BAND 100000",
        "INIT",
        "*OPC?",
        "CALC1:MARK1:FUNC:POW:RES? CPOW",
    ]


def measure_through_interfaces(source: LevelSource, meter: PowerMeter) -> PeakReading:
    """Take a peak reading using only the interfaces, as bench tests do."""
    source.configure(frequency_hz=1e9, level_dbm=-10.0)
    source.set_output(on=True)
    return meter.measure_peak()


def test_drivers_work_through_their_interfaces() -> None:
    """The generator works as a LevelSource and the analyzer as a PowerMeter."""
    analyzer_session = FakeSession({"CALC1:MARK1:X?": "1000000000", "CALC1:MARK1:Y?": "-11.5"})
    reading = measure_through_interfaces(
        SignalGenerator(FakeSession()), SpectrumAnalyzer(analyzer_session)
    )
    assert reading == PeakReading(frequency_hz=1e9, level_dbm=-11.5)


@pytest.mark.parametrize(("reply", "expected"), [("0", False), ("OFF", False), ("ON", True)])
def test_rf_state_accepts_words_and_digits(reply: str, expected: bool) -> None:
    """OUTP? may answer 0/1 or OFF/ON."""
    assert SignalGenerator(FakeSession({"OUTP?": reply})).output_on() is expected


@pytest.mark.parametrize("reply", ["nan", "inf"])
def test_non_finite_numbers_are_unexpected(reply: str) -> None:
    """A reading of NaN or infinity is reported, never passed on as a number."""
    with pytest.raises(InstrumentError, match="Unexpected reply"):
        SignalGenerator(FakeSession({"SOUR:POW?": reply})).level_dbm()


def test_the_generator_reads_and_sets_its_level_offset() -> None:
    """The offset is read and set with the SCPI command real generators use."""
    session = FakeSession({"SOUR:POW:OFFS?": "-1.5"})
    generator = SignalGenerator(session)
    assert generator.offset_db() == pytest.approx(-1.5)
    generator.set_offset_db(-2.25)
    assert session.sent == ["SOUR:POW:OFFS?", "SOUR:POW:OFFS -2.25", "*OPC?"]


def test_the_analyzer_sets_its_channel_bandwidth() -> None:
    """The channel bandwidth is set and the command group waited for."""
    session = FakeSession()
    SpectrumAnalyzer(session).set_channel_bandwidth_hz(1e5)
    assert session.sent == ["POW:ACH:BAND 100000", "*OPC?"]
