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

SPAN_HZ = 1e6
"""The span every measurement sweeps: wide enough for the signal, narrow enough to resolve it."""

RBW_HZ = 1e3
"""The resolution bandwidth the level measurements use."""

HARMONIC_LIMIT_DBC = -40.0
"""How far below the carrier the second harmonic must stay, as a generator specifies it."""

RESET_LEVEL_TOLERANCE_DB = 0.01
"""How far the level after a reset may sit from its default: it is a setting, not a reading."""

CARRIER_TO_FLOOR_MINIMUM_DB = 60.0
"""How far the carrier must stand above the floor once the output is switched on."""

OUT_OF_RANGE_LEVEL_DBM = 25.0
"""A level above the generator's +20 dBm maximum."""

DEFAULT_LEVEL_DBM = -30.0
"""The generator's level after *RST."""


def frequency_label(frequency_hz: float) -> str:
    """Name a frequency for a measurement name.

    Args:
        frequency_hz: The frequency.

    Returns:
        Its value in whole MHz, for example '2400mhz'.
    """
    return f"{frequency_hz / 1e6:.0f}mhz"


def level_label(level_dbm: float) -> str:
    """Name a level for a measurement name.

    Args:
        level_dbm: The level.

    Returns:
        For example 'minus10dbm' or '0dbm'; 'minus' avoids a sign in the name.
    """
    size = f"{abs(level_dbm):g}".replace(".", "p")
    return f"minus{size}dbm" if level_dbm < 0 else f"{size}dbm"


def carrier_level_and_frequency(
    generator: SignalGenerator,
    analyzer: SpectrumAnalyzer,
    expected_level_dbm: float,
    *,
    frequency_hz: float = CARRIER_FREQUENCY_HZ,
    level_dbm: float = CARRIER_LEVEL_DBM,
    tolerance_db: float = LEVEL_TOLERANCE_DB,
) -> tuple[Measurement, Measurement]:
    """Measure a carrier's peak level and frequency.

    Args:
        generator: The generator driver.
        analyzer: The analyzer driver.
        expected_level_dbm: What the analyzer should read after the cable.
        frequency_hz: The carrier frequency.
        level_dbm: The generator level.
        tolerance_db: How far the reading may sit from what is expected.

    Returns:
        The level and frequency measurements, with their limits.
    """
    peak = measure_peak_at(generator, analyzer, frequency_hz, level_dbm)
    versions = firmware_versions(generator, analyzer)
    name = f"{frequency_label(frequency_hz)}_{level_label(level_dbm)}"
    return (
        Measurement(
            f"level_{name}",
            peak.level_dbm,
            "dBm",
            Limit.window(expected_level_dbm, tolerance_db),
            versions,
        ),
        Measurement(
            f"frequency_{name}",
            peak.frequency_hz,
            "Hz",
            Limit.window(frequency_hz, FREQUENCY_TOLERANCE_HZ),
            versions,
        ),
    )


def reset_defaults(generator: SignalGenerator) -> tuple[Measurement, Measurement]:
    """Read the generator's frequency and level after a reset, against their default values.

    Args:
        generator: The generator driver, on a session that has just sent *RST.

    Returns:
        The frequency and level readings, each with a tight limit around its default.
    """
    versions = {"generator": generator.identity().firmware_version}
    return (
        Measurement(
            "reset_frequency",
            generator.frequency_hz(),
            "Hz",
            Limit.window(CARRIER_FREQUENCY_HZ, FREQUENCY_TOLERANCE_HZ),
            versions,
        ),
        Measurement(
            "reset_level",
            generator.level_dbm(),
            "dBm",
            Limit.window(DEFAULT_LEVEL_DBM, RESET_LEVEL_TOLERANCE_DB),
            versions,
        ),
    )


def second_harmonic_suppression(
    generator: SignalGenerator,
    analyzer: SpectrumAnalyzer,
    frequency_hz: float = CARRIER_FREQUENCY_HZ,
    level_dbm: float = CARRIER_LEVEL_DBM,
) -> Measurement:
    """Measure how far the second harmonic sits below the carrier.

    The carrier is measured first, then the analyzer is tuned to twice the frequency, so the
    result is a ratio and the cable's loss at each frequency is part of it, as on a bench.

    Args:
        generator: The generator driver.
        analyzer: The analyzer driver.
        frequency_hz: The carrier frequency.
        level_dbm: The generator level.

    Returns:
        The harmonic's level relative to the carrier, which must stay below the limit.
    """
    carrier = measure_peak_at(generator, analyzer, frequency_hz, level_dbm)
    analyzer.configure(centre_hz=2 * frequency_hz, span_hz=SPAN_HZ, rbw_hz=RBW_HZ)
    harmonic = analyzer.measure_peak()
    return Measurement(
        f"second_harmonic_{frequency_label(frequency_hz)}",
        harmonic.level_dbm - carrier.level_dbm,
        "dBc",
        Limit.upper(HARMONIC_LIMIT_DBC),
        firmware_versions(generator, analyzer),
    )


def carrier_to_floor(
    generator: SignalGenerator,
    analyzer: SpectrumAnalyzer,
    frequency_hz: float = CARRIER_FREQUENCY_HZ,
    level_dbm: float = CARRIER_LEVEL_DBM,
) -> Measurement:
    """Measure how far switching the output on lifts the reading above the floor.

    Args:
        generator: The generator driver.
        analyzer: The analyzer driver.
        frequency_hz: The carrier frequency.
        level_dbm: The generator level.

    Returns:
        The difference between the readings with the output on and off, which must be at
        least the minimum: a generator whose output doesn't switch fails it.
    """
    with_output_on = measure_peak_at(generator, analyzer, frequency_hz, level_dbm)
    generator.set_output(on=False)
    with_output_off = analyzer.measure_peak()
    return Measurement(
        f"carrier_to_floor_{frequency_label(frequency_hz)}",
        with_output_on.level_dbm - with_output_off.level_dbm,
        "dB",
        Limit.lower(CARRIER_TO_FLOOR_MINIMUM_DB),
        firmware_versions(generator, analyzer),
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


def noise_floor_with_rf_off(
    generator: SignalGenerator,
    analyzer: SpectrumAnalyzer,
    rbw_hz: float = RBW_HZ,
    limit_dbm: float = NOISE_FLOOR_LIMIT_DBM,
) -> Measurement:
    """Measure the analyzer's peak with the generator's RF off.

    A wider resolution bandwidth lets more noise through, so the limit belongs to the
    bandwidth and both are passed in together.

    Args:
        generator: The generator driver.
        analyzer: The analyzer driver.
        rbw_hz: The resolution bandwidth to sweep with.
        limit_dbm: The highest peak allowed at that bandwidth.

    Returns:
        The noise-floor measurement, with its limit.
    """
    generator.set_output(on=False)
    analyzer.configure(centre_hz=CARRIER_FREQUENCY_HZ, span_hz=SPAN_HZ, rbw_hz=rbw_hz)
    return Measurement(
        f"noise_floor_{rbw_hz / 1e3:.0f}khz_rbw",
        analyzer.measure_peak().level_dbm,
        "dBm",
        Limit.upper(limit_dbm),
        firmware_versions(generator, analyzer),
    )


def check_command_is_rejected(generator: SignalGenerator, command: str, code: str) -> None:
    """Send a command the instrument must reject, and say what happened when it doesn't.

    Args:
        generator: The generator driver.
        command: The command to send.
        code: The SCPI error code expected in the instrument's reply, for example '-113'.
    """
    try:
        generator.session.write(command)
    except InstrumentError as error:
        if code not in str(error):
            pytest.fail(f"{command!r}: expected error {code}, the instrument reported: {error}")
        return
    pytest.fail(f"{command!r}: the instrument accepted it, but {code} was expected")


def check_out_of_range_level_is_rejected(generator: SignalGenerator) -> None:
    """Check that a level beyond range raises the instrument's -222 and leaves the level alone.

    Args:
        generator: The generator driver, freshly reset.
    """
    command = f"SOUR:POW {OUT_OF_RANGE_LEVEL_DBM:g}"
    check_command_is_rejected(generator, command, "-222")
    level_dbm = generator.level_dbm()
    assert level_dbm == DEFAULT_LEVEL_DBM, (
        f"{command!r} was rejected but still changed the level: it reads {level_dbm:g} dBm, "
        f"expected {DEFAULT_LEVEL_DBM:g} dBm"
    )
