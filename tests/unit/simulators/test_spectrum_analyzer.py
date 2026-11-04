"""Tests for the simulated spectrum analyzer's SCPI commands and measurements."""

from dataclasses import dataclass

import pytest

from simulators.common.physics import (
    BenchParameters,
    PhysicsModel,
    SourceState,
    cable_loss_db,
    flatness_error_db,
    noise_floor_dbm,
)
from simulators.spectrum_analyzer.instrument import SpectrumAnalyzerScpi

PARAMETERS = BenchParameters()
TONE = SourceState(frequency_hz=1e9, level_dbm=-10.0, output_on=True)
EXPECTED_TONE_DBM = (
    TONE.level_dbm
    - cable_loss_db(TONE.frequency_hz, PARAMETERS.cable_loss_at_1ghz_db)
    + flatness_error_db(
        TONE.frequency_hz, PARAMETERS.flatness_ripple_db, PARAMETERS.flatness_period_hz
    )
)
LEVEL_TOLERANCE_DB = 0.1
"""Five times the 0.02 dB reading spread."""


@dataclass
class FakeGenerator:
    """Stands in for the generator's admin port: reports whatever state the test sets.

    Attributes:
        state: What the generator is sending.
    """

    state: SourceState = TONE

    def source_state(self) -> SourceState:
        """Report the state the test set.

        Returns:
            The current state.
        """
        return self.state


def make_analyzer(generator: FakeGenerator | None = None) -> SpectrumAnalyzerScpi:
    """Return an analyzer with a known firmware version, measuring the given generator."""
    return SpectrumAnalyzerScpi(
        firmware_version="1.2.3",
        generator=generator or FakeGenerator(),
        physics=PhysicsModel(PARAMETERS),
    )


def errors_of(analyzer: SpectrumAnalyzerScpi) -> list[str]:
    """Read the error queue until it reports no error, and return what was in it."""
    errors: list[str] = []
    while (entry := analyzer.process("SYST:ERR?")[0]) != '0,"No error"':
        errors.append(entry)
    return errors


def peak(analyzer: SpectrumAnalyzerScpi) -> tuple[float, float]:
    """Run a peak search and return the marker's frequency and level."""
    analyzer.process("CALC:MARK:MAX")
    frequency, level = analyzer.process("CALC:MARK:X?;CALC:MARK:Y?")
    return float(frequency), float(level)


def test_identity_reports_model_and_firmware_version() -> None:
    """*IDN? names VirtualRF, the SA-1 model, the serial number and the firmware version."""
    assert make_analyzer().process("*IDN?") == ["VirtualRF,SA-1,200001,1.2.3"]


def test_starts_at_its_defaults() -> None:
    """Centre 1 GHz, span 1 MHz, RBW 1 kHz, channel 100 kHz, continuous sweep."""
    analyzer = make_analyzer()
    assert analyzer.process("FREQ:CENT?;FREQ:SPAN?;BAND?;POW:ACH:BAND?;INIT:CONT?") == [
        "1000000000",
        "1000000",
        "1000",
        "100000",
        "1",
    ]


@pytest.mark.parametrize(
    ("command", "query", "expected"),
    [
        ("SENS:FREQ:CENT 2.4 GHz", "FREQ:CENT?", "2400000000"),
        ("FREQ:SPAN 20 MHz", "SENSe:FREQuency:SPAN?", "20000000"),
        ("SENSe:BANDwidth:RESolution 10 kHz", "BAND?", "10000"),
        ("POW:ACH:BAND:CHAN1 1 MHz", "SENS:POW:ACH:BAND?", "1000000"),
        ("INIT:CONT OFF", "INIT:CONT?", "0"),
    ],
)
def test_settings_can_be_changed(command: str, query: str, expected: str) -> None:
    """Each setting accepts units and short or long forms, and reads back."""
    analyzer = make_analyzer()
    analyzer.process(command)
    assert analyzer.process(query) == [expected]
    assert not errors_of(analyzer)


@pytest.mark.parametrize(
    "command",
    ["FREQ:CENT 8 GHz", "FREQ:CENT 1 kHz", "FREQ:SPAN 1 Hz", "BAND 20 MHz", "POW:ACH:BAND 1 Hz"],
)
def test_out_of_range_settings_are_rejected(command: str) -> None:
    """Values beyond the analyzer's ranges are -222 and change nothing."""
    analyzer = make_analyzer()
    analyzer.process(command)
    assert errors_of(analyzer)[0].startswith("-222")
    assert analyzer.process("FREQ:CENT?;FREQ:SPAN?;BAND?;POW:ACH:BAND?") == [
        "1000000000",
        "1000000",
        "1000",
        "100000",
    ]


def test_peak_search_finds_the_tone() -> None:
    """The peak marker reads the tone's level after the cable, at the tone's frequency."""
    frequency_hz, level_dbm = peak(make_analyzer())
    assert level_dbm == pytest.approx(EXPECTED_TONE_DBM, abs=LEVEL_TOLERANCE_DB)
    assert frequency_hz == pytest.approx(1e9, abs=1e6 / 2000)


def test_marker_frequency_snaps_to_a_sweep_point() -> None:
    """The marker sits on one of 1001 sweep points, so its frequency is quantised."""
    generator = FakeGenerator(
        SourceState(frequency_hz=1.0001234e9, level_dbm=-10.0, output_on=True)
    )
    frequency_hz, _ = peak(make_analyzer(generator))
    step_hz = 1e6 / 1000
    assert (frequency_hz - (1e9 - 0.5e6)) / step_hz == pytest.approx(
        round((frequency_hz - (1e9 - 0.5e6)) / step_hz)
    )
    assert abs(frequency_hz - 1.0001234e9) <= step_hz / 2


@pytest.mark.parametrize(
    "state",
    [
        SourceState(frequency_hz=1.2e9, level_dbm=-10.0, output_on=True),
        SourceState(frequency_hz=1e9, level_dbm=-10.0, output_on=False),
    ],
)
def test_without_a_tone_in_span_the_marker_sits_on_the_noise(state: SourceState) -> None:
    """With the tone outside the span or RF off, the peak is noise near the floor, at centre."""
    frequency_hz, level_dbm = peak(make_analyzer(FakeGenerator(state)))
    assert level_dbm == pytest.approx(noise_floor_dbm(1e3, PARAMETERS.noise_figure_db), abs=2.5)
    assert frequency_hz == pytest.approx(1e9)


def test_wider_rbw_raises_the_noise_reading() -> None:
    """With RF off, a 100 times wider RBW reads about 20 dB more noise."""
    analyzer = make_analyzer(FakeGenerator(SourceState(1e9, -10.0, output_on=False)))
    _, narrow_dbm = peak(analyzer)
    analyzer.process("BAND 100 kHz")
    _, wide_dbm = peak(analyzer)
    assert wide_dbm - narrow_dbm == pytest.approx(20.0, abs=3.0)


def test_continuous_sweep_follows_the_generator() -> None:
    """In continuous mode, each peak search sees the generator's current level."""
    generator = FakeGenerator()
    analyzer = make_analyzer(generator)
    peak(analyzer)
    generator.state = SourceState(frequency_hz=1e9, level_dbm=-20.0, output_on=True)
    _, level_dbm = peak(analyzer)
    assert level_dbm == pytest.approx(EXPECTED_TONE_DBM - 10.0, abs=LEVEL_TOLERANCE_DB)


def test_single_sweep_needs_init_and_holds_its_result() -> None:
    """With continuous sweep off, a reading needs INIT and only changes at the next INIT."""
    generator = FakeGenerator()
    analyzer = make_analyzer(generator)
    analyzer.process("INIT:CONT OFF")
    analyzer.process("CALC:MARK:MAX")
    assert errors_of(analyzer) == ['-230,"Data corrupt or stale;no sweep yet; send INIT"']
    analyzer.process("INIT;*OPC?")
    _, first_dbm = peak(analyzer)
    generator.state = SourceState(frequency_hz=1e9, level_dbm=-20.0, output_on=True)
    _, held_dbm = peak(analyzer)
    analyzer.process("INIT")
    _, new_dbm = peak(analyzer)
    assert held_dbm == first_dbm
    assert new_dbm == pytest.approx(EXPECTED_TONE_DBM - 10.0, abs=LEVEL_TOLERANCE_DB)


def test_marker_needs_a_peak_search_first() -> None:
    """Reading a marker before any peak search is -221 settings conflict."""
    analyzer = make_analyzer()
    assert not analyzer.process("CALC:MARK:Y?")
    assert errors_of(analyzer) == ['-221,"Settings conflict;no peak search since *RST"']


def test_suffix_one_is_accepted_and_others_are_not() -> None:
    """CALC1:MARK1 is the same marker; window or marker 2 doesn't exist."""
    analyzer = make_analyzer()
    analyzer.process("CALC1:MARK1:MAX")
    assert len(analyzer.process("CALC1:MARK1:Y?")) == 1
    analyzer.process("CALC2:MARK:MAX")
    assert errors_of(analyzer) == ['-113,"Undefined header"']


def test_channel_power_reads_the_tone_in_the_channel() -> None:
    """Channel power around a strong tone is the tone's level after the cable."""
    analyzer = make_analyzer()
    replies = analyzer.process("CALC:MARK:FUNC:POW:RES? CPOW")
    assert len(replies) == 1
    result = replies[0]
    assert float(result) == pytest.approx(EXPECTED_TONE_DBM, abs=LEVEL_TOLERANCE_DB)


def test_channel_power_without_a_tone_is_channel_noise() -> None:
    """Channel power with RF off is the noise in the 100 kHz channel, about -100 dBm."""
    analyzer = make_analyzer(FakeGenerator(SourceState(1e9, -10.0, output_on=False)))
    replies = analyzer.process("CALCulate1:MARKer1:FUNCtion:POWer:RESult? CPOWer")
    assert len(replies) == 1
    result = replies[0]
    assert float(result) == pytest.approx(noise_floor_dbm(1e5, PARAMETERS.noise_figure_db), abs=2.5)


@pytest.mark.parametrize(
    ("command", "error"),
    [
        ("CALC:MARK:FUNC:POW:RES? ACP", '-224,"Illegal parameter value;ACP"'),
        ("CALC:MARK:FUNC:POW:RES?", '-109,"Missing parameter"'),
    ],
)
def test_channel_power_needs_the_cpower_keyword(command: str, error: str) -> None:
    """The result query takes exactly the CPOWer keyword."""
    analyzer = make_analyzer()
    assert not analyzer.process(command)
    assert errors_of(analyzer) == [error]


def test_reset_restores_defaults_and_repeats_readings() -> None:
    """*RST restores the settings and reseeds the noise, so the same readings come back."""
    analyzer = make_analyzer()
    first = peak(analyzer)
    analyzer.process("FREQ:CENT 2 GHz;BAND 10 kHz;INIT:CONT OFF;INIT")
    analyzer.process("*RST")
    assert analyzer.process("FREQ:CENT?;BAND?;INIT:CONT?") == ["1000000000", "1000", "1"]
    assert not analyzer.process("CALC:MARK:Y?")
    errors_of(analyzer)
    assert peak(analyzer) == first


def test_channel_power_ignores_a_tone_outside_the_span() -> None:
    """A tone inside the channel but outside the swept span isn't measured."""
    generator = FakeGenerator(SourceState(frequency_hz=1e9 + 30e3, level_dbm=-10.0, output_on=True))
    analyzer = make_analyzer(generator)
    analyzer.process("FREQ:SPAN 10 kHz")
    replies = analyzer.process("CALC:MARK:FUNC:POW:RES? CPOW")
    assert len(replies) == 1
    assert float(replies[0]) == pytest.approx(
        noise_floor_dbm(1e4, PARAMETERS.noise_figure_db), abs=2.5
    )


@pytest.mark.parametrize(
    ("span", "channel", "integrated_hz"),
    [
        ("1 kHz", "100 kHz", 1e3),
        ("10 Hz", "100 MHz", 10.0),
        ("1 MHz", "100 kHz", 1e5),
        ("100 kHz", "100 kHz", 1e5),
    ],
)
def test_channel_power_noise_covers_only_the_swept_part_of_the_channel(
    span: str, channel: str, integrated_hz: float
) -> None:
    """With RF off, channel power is the noise in whichever is narrower: the channel or the span."""
    analyzer = make_analyzer(FakeGenerator(SourceState(1e9, -10.0, output_on=False)))
    analyzer.process(f"FREQ:SPAN {span};POW:ACH:BAND {channel}")
    assert not errors_of(analyzer)
    replies = analyzer.process("CALC:MARK:FUNC:POW:RES? CPOW")
    assert float(replies[0]) == pytest.approx(
        noise_floor_dbm(integrated_hz, PARAMETERS.noise_figure_db), abs=2.5
    )


def test_channel_power_wider_than_the_span_still_reads_a_tone_in_view() -> None:
    """A tone inside the span is still measured when the channel is wider than the span."""
    analyzer = make_analyzer()
    analyzer.process("FREQ:SPAN 10 kHz;POW:ACH:BAND 1 MHz")
    replies = analyzer.process("CALC:MARK:FUNC:POW:RES? CPOW")
    assert float(replies[0]) == pytest.approx(EXPECTED_TONE_DBM, abs=LEVEL_TOLERANCE_DB)


@pytest.mark.parametrize(
    ("line", "reply_count", "errors"),
    [
        ("CALC:MARK:X?;CALC:MARK:Y?", 2, []),
        ("CALC:MARK:X?;:CALC:MARK:Y?", 2, []),
        ("CALC:MARK:X?;Y?", 1, ['-113,"Undefined header"']),
    ],
)
def test_each_command_after_a_semicolon_needs_its_full_path(
    line: str, reply_count: int, errors: list[str]
) -> None:
    """Every command in a compound line is read from the root; relative headers aren't supported."""
    analyzer = make_analyzer()
    analyzer.process("CALC:MARK:MAX")
    assert len(analyzer.process(line)) == reply_count
    assert errors_of(analyzer) == errors


def test_the_second_harmonic_is_measured_at_twice_the_carrier_frequency() -> None:
    """Tuned to 2 GHz with a 1 GHz carrier, the analyzer sees the harmonic below it."""
    analyzer = make_analyzer()
    carrier_dbm = peak(analyzer)[1]
    analyzer.process("FREQ:CENT 2 GHz")
    harmonic_dbm = peak(analyzer)[1]
    assert harmonic_dbm < carrier_dbm + PARAMETERS.second_harmonic_dbc
    assert harmonic_dbm > carrier_dbm + PARAMETERS.second_harmonic_dbc - 5


def test_the_carrier_is_still_what_a_span_around_it_measures() -> None:
    """Adding a harmonic doesn't change what the analyzer reads at the carrier."""
    analyzer = make_analyzer()
    assert peak(analyzer)[1] == pytest.approx(EXPECTED_TONE_DBM, abs=LEVEL_TOLERANCE_DB)


def test_a_span_holding_neither_signal_reads_the_noise_floor() -> None:
    """Between the carrier and its harmonic there is only noise."""
    analyzer = make_analyzer()
    analyzer.process("FREQ:CENT 1.5 GHz")
    assert peak(analyzer)[1] == pytest.approx(
        noise_floor_dbm(1e3, PARAMETERS.noise_figure_db), abs=2.5
    )


def test_the_marker_sits_on_whichever_signal_is_in_the_span() -> None:
    """With the harmonic in view the marker reports its frequency, not the carrier's."""
    analyzer = make_analyzer()
    analyzer.process("FREQ:CENT 2 GHz")
    assert peak(analyzer)[0] == pytest.approx(2e9, abs=1e3)
