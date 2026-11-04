"""The checks both bench suites run, each with its limit, defined once.

The in-process suite and the container suite take the same measurements against the same
limits. They differ only in the level they expect after the cable: in-process the tests know
the exact physics, against the containers only the cable's datasheet loss. So that is passed in.
"""

import pytest

from drivers.session import InstrumentError
from drivers.signal_generator import SignalGenerator
from drivers.spectrum_analyzer import SpectrumAnalyzer
from measurements.record import Limit, Measurement
from tests.utilities.bench import firmware_versions, measure_peak_at

CARRIER_FREQUENCY_HZ = 1e9
"""The carrier the bench measurements use."""

CARRIER_LEVEL_DBM = -10.0
"""The generator level for the carrier."""

LEVEL_TOLERANCE_DB = 0.5
"""Allowed level error: a typical spec-sheet level accuracy."""

FREQUENCY_TOLERANCE_HZ = 1e3
"""Allowed frequency error for a 1 MHz span: one of the 1000 steps between sweep points."""

CHANNEL_BANDWIDTH_HZ = 1e5
"""The channel-power integration bandwidth around the carrier."""

NOISE_FLOOR_LIMIT_DBM = -110.0
"""With RF off and a 1 kHz RBW, the analyzer's peak must stay below this."""

OUT_OF_RANGE_LEVEL_DBM = 25.0
"""A level above the generator's +20 dBm maximum."""

DEFAULT_LEVEL_DBM = -30.0
"""The generator's level after *RST."""


def carrier_level_and_frequency(
    generator: SignalGenerator, analyzer: SpectrumAnalyzer, expected_level_dbm: float
) -> tuple[Measurement, Measurement]:
    """Measure the carrier's peak level and frequency.

    Args:
        generator: The generator driver.
        analyzer: The analyzer driver.
        expected_level_dbm: What the analyzer should read after the cable.

    Returns:
        The level and frequency measurements, with their limits.
    """
    peak = measure_peak_at(generator, analyzer, CARRIER_FREQUENCY_HZ, CARRIER_LEVEL_DBM)
    versions = firmware_versions(generator, analyzer)
    return (
        Measurement(
            "level_1ghz_minus10dbm",
            peak.level_dbm,
            "dBm",
            Limit.window(expected_level_dbm, LEVEL_TOLERANCE_DB),
            versions,
        ),
        Measurement(
            "frequency_1ghz",
            peak.frequency_hz,
            "Hz",
            Limit.window(CARRIER_FREQUENCY_HZ, FREQUENCY_TOLERANCE_HZ),
            versions,
        ),
    )


def carrier_channel_power(
    generator: SignalGenerator, analyzer: SpectrumAnalyzer, expected_level_dbm: float
) -> Measurement:
    """Measure the channel power around the carrier, which should match the carrier's level.

    Args:
        generator: The generator driver.
        analyzer: The analyzer driver.
        expected_level_dbm: What the analyzer should read after the cable.

    Returns:
        The channel-power measurement, with its limit.
    """
    measure_peak_at(generator, analyzer, CARRIER_FREQUENCY_HZ, CARRIER_LEVEL_DBM)
    return Measurement(
        "channel_power_1ghz_100khz",
        analyzer.measure_channel_power_dbm(channel_bandwidth_hz=CHANNEL_BANDWIDTH_HZ),
        "dBm",
        Limit.window(expected_level_dbm, LEVEL_TOLERANCE_DB),
        firmware_versions(generator, analyzer),
    )


def noise_floor_with_rf_off(generator: SignalGenerator, analyzer: SpectrumAnalyzer) -> Measurement:
    """Measure the analyzer's peak with the generator's RF off.

    Args:
        generator: The generator driver.
        analyzer: The analyzer driver.

    Returns:
        The noise-floor measurement, with its limit.
    """
    generator.set_output(on=False)
    analyzer.configure(centre_hz=CARRIER_FREQUENCY_HZ, span_hz=1e6, rbw_hz=1e3)
    return Measurement(
        "noise_floor_1khz_rbw",
        analyzer.measure_peak().level_dbm,
        "dBm",
        Limit.upper(NOISE_FLOOR_LIMIT_DBM),
        firmware_versions(generator, analyzer),
    )


def check_out_of_range_level_is_rejected(generator: SignalGenerator) -> None:
    """Check that a level beyond range raises the instrument's -222 and leaves the level alone.

    Args:
        generator: The generator driver, freshly reset.
    """
    with pytest.raises(InstrumentError, match="-222"):
        generator.configure(frequency_hz=CARRIER_FREQUENCY_HZ, level_dbm=OUT_OF_RANGE_LEVEL_DBM)
    assert generator.level_dbm() == DEFAULT_LEVEL_DBM
