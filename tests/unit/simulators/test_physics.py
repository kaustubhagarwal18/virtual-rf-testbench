"""Tests for the bench physics model: cable loss, flatness, noise floor and readings."""

import math
import statistics
from dataclasses import replace

import pytest

from simulators.common.physics import (
    BenchParameters,
    PhysicsModel,
    SourceState,
    cable_loss_db,
    flatness_error_db,
    noise_floor_dbm,
    power_sum_dbm,
)

PARAMETERS = BenchParameters()
STRONG_TONE = SourceState(frequency_hz=1e9, level_dbm=-10.0, output_on=True)
RBW_HZ = 1e3


def test_cable_loss_at_one_gigahertz_is_the_rated_loss() -> None:
    """At 1 GHz the cable loses exactly its rated loss."""
    assert cable_loss_db(1e9, loss_at_1ghz_db=1.5) == pytest.approx(1.5)


def test_cable_loss_grows_with_the_square_root_of_frequency() -> None:
    """Four times the frequency doubles the loss, as skin effect predicts."""
    assert cable_loss_db(4e9, loss_at_1ghz_db=1.5) == pytest.approx(3.0)


@pytest.mark.parametrize("frequency_hz", [0.0, -1e6])
def test_cable_loss_needs_a_positive_frequency(frequency_hz: float) -> None:
    """A zero or negative frequency is a programming error."""
    with pytest.raises(ValueError, match="frequency"):
        cable_loss_db(frequency_hz, loss_at_1ghz_db=1.5)


def test_flatness_error_stays_within_the_ripple() -> None:
    """The flatness error reaches, but never exceeds, the ripple amplitude."""
    ripple_db = 0.15
    errors = [
        flatness_error_db(frequency_hz, ripple_db=ripple_db, period_hz=1.7e9)
        for frequency_hz in range(10_000_000, 6_000_000_000, 7_000_000)
    ]
    assert max(abs(error) for error in errors) <= ripple_db
    assert max(errors) == pytest.approx(ripple_db, rel=0.01)


def test_flatness_error_is_smooth() -> None:
    """Frequencies 1 MHz apart have almost the same flatness error."""
    first = flatness_error_db(2.0e9, ripple_db=0.15, period_hz=1.7e9)
    second = flatness_error_db(2.001e9, ripple_db=0.15, period_hz=1.7e9)
    assert first == pytest.approx(second, abs=0.001)


def test_noise_floor_for_one_kilohertz_rbw() -> None:
    """-174 dBm/Hz, plus a 24 dB noise figure, plus 30 dB for 1 kHz, gives -120 dBm."""
    assert noise_floor_dbm(1e3, noise_figure_db=24.0) == pytest.approx(-120.0)


def test_ten_times_the_rbw_raises_the_floor_by_ten_db() -> None:
    """The noise floor rises 10 dB for every tenfold increase in RBW."""
    narrow = noise_floor_dbm(1e3, noise_figure_db=24.0)
    wide = noise_floor_dbm(1e4, noise_figure_db=24.0)
    assert wide - narrow == pytest.approx(10.0)


def test_noise_floor_needs_a_positive_rbw() -> None:
    """A zero RBW is a programming error."""
    with pytest.raises(ValueError, match="RBW"):
        noise_floor_dbm(0.0, noise_figure_db=24.0)


def test_two_equal_powers_add_three_db() -> None:
    """Two equal signals add up to 3.01 dB more than either one."""
    assert power_sum_dbm(-50.0, -50.0) == pytest.approx(-50.0 + 10 * math.log10(2))


def test_a_much_weaker_power_barely_changes_the_sum() -> None:
    """A signal 40 dB weaker adds less than 0.001 dB."""
    assert power_sum_dbm(-10.0, -50.0) == pytest.approx(-10.0, abs=0.001)


def test_expected_peak_is_level_minus_loss_plus_flatness() -> None:
    """A strong tone reads as its level minus cable loss plus the flatness error."""
    model = PhysicsModel(PARAMETERS)
    expected = (
        STRONG_TONE.level_dbm
        - cable_loss_db(STRONG_TONE.frequency_hz, PARAMETERS.cable_loss_at_1ghz_db)
        + flatness_error_db(
            STRONG_TONE.frequency_hz, PARAMETERS.flatness_ripple_db, PARAMETERS.flatness_period_hz
        )
    )
    assert model.expected_peak_dbm(STRONG_TONE, RBW_HZ) == pytest.approx(expected, abs=0.001)


def test_expected_peak_with_output_off_is_the_noise_floor() -> None:
    """With the generator's RF output off, the analyzer sees only its noise floor."""
    model = PhysicsModel(PARAMETERS)
    silent = SourceState(frequency_hz=1e9, level_dbm=-10.0, output_on=False)
    assert model.expected_peak_dbm(silent, RBW_HZ) == pytest.approx(
        noise_floor_dbm(RBW_HZ, PARAMETERS.noise_figure_db)
    )


def test_tone_near_the_floor_reads_high() -> None:
    """A tone at the noise floor reads 3 dB above it, as on a real analyzer."""
    model = PhysicsModel(PARAMETERS)
    floor = noise_floor_dbm(RBW_HZ, PARAMETERS.noise_figure_db)
    at_floor = SourceState(
        frequency_hz=1e9,
        level_dbm=floor
        + cable_loss_db(1e9, PARAMETERS.cable_loss_at_1ghz_db)
        - flatness_error_db(1e9, PARAMETERS.flatness_ripple_db, PARAMETERS.flatness_period_hz),
        output_on=True,
    )
    assert model.expected_peak_dbm(at_floor, RBW_HZ) == pytest.approx(floor + 3.01, abs=0.01)


def test_same_seed_gives_the_same_readings() -> None:
    """Two models with the same seed produce identical readings."""
    first, second = PhysicsModel(PARAMETERS), PhysicsModel(PARAMETERS)
    assert [first.peak_reading_dbm(STRONG_TONE, RBW_HZ) for _ in range(5)] == [
        second.peak_reading_dbm(STRONG_TONE, RBW_HZ) for _ in range(5)
    ]


def test_different_seeds_give_different_readings() -> None:
    """A different seed gives a different sequence of readings."""
    first = PhysicsModel(PARAMETERS)
    second = PhysicsModel(
        BenchParameters(noise=replace(PARAMETERS.noise, seed=PARAMETERS.noise.seed + 1))
    )
    assert first.peak_reading_dbm(STRONG_TONE, RBW_HZ) != second.peak_reading_dbm(
        STRONG_TONE, RBW_HZ
    )


def test_reset_repeats_the_readings() -> None:
    """After reset, the model gives the same readings again, as after *RST."""
    model = PhysicsModel(PARAMETERS)
    before = [model.peak_reading_dbm(STRONG_TONE, RBW_HZ) for _ in range(3)]
    model.reset()
    assert [model.peak_reading_dbm(STRONG_TONE, RBW_HZ) for _ in range(3)] == before


def test_strong_tone_readings_scatter_by_the_repeatability() -> None:
    """Strong-tone readings centre on the expected level with a 0.02 dB spread."""
    model = PhysicsModel(PARAMETERS)
    readings = [model.peak_reading_dbm(STRONG_TONE, RBW_HZ) for _ in range(2000)]
    assert statistics.fmean(readings) == pytest.approx(
        model.expected_peak_dbm(STRONG_TONE, RBW_HZ), abs=0.005
    )
    assert statistics.stdev(readings) == pytest.approx(PARAMETERS.noise.repeatability_db, rel=0.2)


def test_noise_floor_readings_scatter_more() -> None:
    """With RF off, readings scatter by the wider noise-floor spread."""
    model = PhysicsModel(PARAMETERS)
    silent = SourceState(frequency_hz=1e9, level_dbm=-10.0, output_on=False)
    readings = [model.peak_reading_dbm(silent, RBW_HZ) for _ in range(2000)]
    assert statistics.stdev(readings) == pytest.approx(PARAMETERS.noise.floor_spread_db, rel=0.2)


CHANNEL_BANDWIDTH_HZ = 1e5


def test_strong_tone_in_the_channel_sets_the_channel_power() -> None:
    """A strong tone inside the channel dominates its power: level minus loss plus flatness."""
    model = PhysicsModel(PARAMETERS)
    expected = model.expected_peak_dbm(STRONG_TONE, RBW_HZ)
    assert model.expected_channel_power_dbm(
        STRONG_TONE, centre_hz=1e9, channel_bandwidth_hz=CHANNEL_BANDWIDTH_HZ
    ) == pytest.approx(expected, abs=0.001)


@pytest.mark.parametrize(
    "source",
    [
        SourceState(frequency_hz=1.2e9, level_dbm=-10.0, output_on=True),
        SourceState(frequency_hz=1e9, level_dbm=-10.0, output_on=False),
    ],
)
def test_channel_without_a_tone_holds_only_noise(source: SourceState) -> None:
    """With the tone outside the channel or RF off, channel power is the noise in the channel."""
    model = PhysicsModel(PARAMETERS)
    assert model.expected_channel_power_dbm(
        source, centre_hz=1e9, channel_bandwidth_hz=CHANNEL_BANDWIDTH_HZ
    ) == pytest.approx(noise_floor_dbm(CHANNEL_BANDWIDTH_HZ, PARAMETERS.noise_figure_db))


def test_channel_power_readings_scatter_by_the_repeatability() -> None:
    """Channel power readings on a strong tone have the 0.02 dB spread."""
    model = PhysicsModel(PARAMETERS)
    readings = [
        model.channel_power_reading_dbm(STRONG_TONE, 1e9, CHANNEL_BANDWIDTH_HZ) for _ in range(2000)
    ]
    assert statistics.stdev(readings) == pytest.approx(PARAMETERS.noise.repeatability_db, rel=0.2)


def test_tone_visibility_needs_ten_db_above_the_floor() -> None:
    """A tone is visible when it stands 10 dB or more above the noise floor."""
    model = PhysicsModel(PARAMETERS)
    floor = noise_floor_dbm(RBW_HZ, PARAMETERS.noise_figure_db)
    weak = SourceState(frequency_hz=1e9, level_dbm=floor + 5.0, output_on=True)
    assert model.tone_is_visible(STRONG_TONE, RBW_HZ)
    assert not model.tone_is_visible(weak, RBW_HZ)


def test_the_generator_puts_a_carrier_and_a_second_harmonic_on_the_cable() -> None:
    """A real generator isn't perfectly clean: there is a weaker tone at twice the frequency."""
    model = PhysicsModel(PARAMETERS)
    carrier, harmonic = model.signals(STRONG_TONE)
    assert carrier == STRONG_TONE
    assert harmonic.frequency_hz == 2 * STRONG_TONE.frequency_hz
    assert harmonic.level_dbm == STRONG_TONE.level_dbm + PARAMETERS.second_harmonic_dbc
    assert harmonic.output_on


def test_nothing_is_on_the_cable_with_the_output_off() -> None:
    """With RF off there is no carrier and no harmonic."""
    assert not PhysicsModel(PARAMETERS).signals(replace(STRONG_TONE, output_on=False))


def test_the_harmonic_suffers_more_cable_loss_than_the_carrier() -> None:
    """At twice the frequency the cable loses more, so the harmonic arrives weaker than its dBc."""
    model = PhysicsModel(PARAMETERS)
    carrier, harmonic = model.signals(STRONG_TONE)
    carrier_at_analyzer = model.expected_peak_dbm(carrier, RBW_HZ)
    harmonic_at_analyzer = model.expected_peak_dbm(harmonic, RBW_HZ)
    suppression_dbc = harmonic_at_analyzer - carrier_at_analyzer
    assert suppression_dbc < PARAMETERS.second_harmonic_dbc
