"""The drivers on the in-process bench: the Week 1 level measurement, recorded with its margin."""

import socket
from collections.abc import Callable, Iterator

import pytest

from drivers.session import (
    InstrumentError,
    InstrumentTimeoutError,
    RsInstrumentSession,
    socket_resource,
)
from drivers.signal_generator import SignalGenerator
from drivers.spectrum_analyzer import SpectrumAnalyzer
from measurements.record import Limit, Measurement
from simulators.common.physics import BenchParameters, cable_loss_db, flatness_error_db
from tests.integration.conftest import RunningBench

LEVEL_TOLERANCE_DB = 0.5
"""Allowed level error: a typical spec-sheet level accuracy."""

FREQUENCY_TOLERANCE_HZ = 1e3
"""Allowed frequency error for a 1 MHz span: one of the 1000 steps between sweep points."""

NOISE_FLOOR_LIMIT_DBM = -110.0
"""With RF off and a 1 kHz RBW, the peak must stay below this."""

Recorder = Callable[..., None]


@pytest.fixture(name="bench")
def fixture_bench(
    running_bench: RunningBench,
) -> Iterator[tuple[SignalGenerator, SpectrumAnalyzer]]:
    """Open driver sessions on the generator and the analyzer, reset, and close them afterwards."""
    with (
        RsInstrumentSession(socket_resource("127.0.0.1", running_bench.generator.scpi_port)) as gen,
        RsInstrumentSession(socket_resource("127.0.0.1", running_bench.analyzer.scpi_port)) as ana,
    ):
        yield SignalGenerator(gen), SpectrumAnalyzer(ana)


def firmware_versions(generator: SignalGenerator, analyzer: SpectrumAnalyzer) -> dict[str, str]:
    """Return both instruments' firmware versions, for the measurement record.

    Args:
        generator: The generator driver.
        analyzer: The analyzer driver.

    Returns:
        The versions by instrument role.
    """
    return {
        "generator": generator.identity().firmware_version,
        "analyzer": analyzer.identity().firmware_version,
    }


def expected_level_dbm(level_dbm: float, frequency_hz: float) -> float:
    """Return what the analyzer should read for a generator level, after the bench's cable.

    Args:
        level_dbm: The generator level.
        frequency_hz: The frequency.

    Returns:
        The level minus cable loss plus the analyzer's flatness error.
    """
    parameters = BenchParameters()
    return (
        level_dbm
        - cable_loss_db(frequency_hz, parameters.cable_loss_at_1ghz_db)
        + flatness_error_db(
            frequency_hz, parameters.flatness_ripple_db, parameters.flatness_period_hz
        )
    )


def test_drivers_identify_both_instruments(bench: tuple[SignalGenerator, SpectrumAnalyzer]) -> None:
    """The drivers read each instrument's model and firmware version."""
    generator, analyzer = bench
    assert (generator.identity().model, analyzer.identity().model) == ("SG-1", "SA-1")
    assert firmware_versions(generator, analyzer) == {"generator": "9.9.9", "analyzer": "9.9.9"}


def test_level_and_frequency_at_1ghz(
    bench: tuple[SignalGenerator, SpectrumAnalyzer], record_measurements: Recorder
) -> None:
    """The analyzer reads -10 dBm minus cable loss within 0.5 dB, at 1 GHz within 1 kHz."""
    generator, analyzer = bench
    generator.configure(frequency_hz=1e9, level_dbm=-10.0)
    generator.set_output(on=True)
    analyzer.configure(centre_hz=1e9, span_hz=1e6, rbw_hz=1e3)
    peak = analyzer.measure_peak()
    versions = firmware_versions(generator, analyzer)
    record_measurements(
        Measurement(
            "level_1ghz_minus10dbm",
            peak.level_dbm,
            "dBm",
            Limit.window(expected_level_dbm(-10.0, 1e9), LEVEL_TOLERANCE_DB),
            versions,
        )
    )
    record_measurements(
        Measurement(
            "frequency_1ghz",
            peak.frequency_hz,
            "Hz",
            Limit.window(1e9, FREQUENCY_TOLERANCE_HZ),
            versions,
        )
    )


def test_channel_power_at_1ghz(
    bench: tuple[SignalGenerator, SpectrumAnalyzer], record_measurements: Recorder
) -> None:
    """Channel power around the carrier matches the carrier level within 0.5 dB."""
    generator, analyzer = bench
    generator.configure(frequency_hz=1e9, level_dbm=-10.0)
    generator.set_output(on=True)
    analyzer.configure(centre_hz=1e9, span_hz=1e6, rbw_hz=1e3)
    record_measurements(
        Measurement(
            "channel_power_1ghz_100khz",
            analyzer.measure_channel_power_dbm(channel_bandwidth_hz=1e5),
            "dBm",
            Limit.window(expected_level_dbm(-10.0, 1e9), LEVEL_TOLERANCE_DB),
            firmware_versions(generator, analyzer),
        )
    )


def test_noise_floor_with_rf_off(
    bench: tuple[SignalGenerator, SpectrumAnalyzer], record_measurements: Recorder
) -> None:
    """With RF off, the analyzer's peak stays below -110 dBm at 1 kHz RBW."""
    generator, analyzer = bench
    generator.set_output(on=False)
    analyzer.configure(centre_hz=1e9, span_hz=1e6, rbw_hz=1e3)
    record_measurements(
        Measurement(
            "noise_floor_1khz_rbw",
            analyzer.measure_peak().level_dbm,
            "dBm",
            Limit.upper(NOISE_FLOOR_LIMIT_DBM),
            firmware_versions(generator, analyzer),
        )
    )


def test_out_of_range_level_is_reported_and_the_session_recovers(
    bench: tuple[SignalGenerator, SpectrumAnalyzer],
) -> None:
    """A level above +20 dBm raises the instrument's -222, and the session still works after."""
    generator, _ = bench
    with pytest.raises(InstrumentError, match="-222"):
        generator.configure(frequency_hz=1e9, level_dbm=25.0)
    default_level_dbm = -30.0
    assert generator.level_dbm() == default_level_dbm


def test_unanswered_query_times_out(running_bench: RunningBench) -> None:
    """An unknown query times out, and the timeout error carries the instrument's -113."""
    resource = socket_resource("127.0.0.1", running_bench.generator.scpi_port)
    with (
        RsInstrumentSession(resource, timeout_ms=300) as session,
        pytest.raises(InstrumentTimeoutError, match="-113"),
    ):
        session.query("BOGus?")


def test_unreachable_instrument_is_an_instrument_error() -> None:
    """Connecting to a port nothing listens on raises InstrumentError, naming the resource."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    with pytest.raises(InstrumentError, match=r"Can't connect to TCPIP::127\.0\.0\.1"):
        RsInstrumentSession(socket_resource("127.0.0.1", closed_port), timeout_ms=500)
