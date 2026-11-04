"""The drivers on the in-process bench: the level measurement, recorded with its margin."""

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
from simulators.common.physics import BenchParameters, cable_loss_db, flatness_error_db
from tests.integration.conftest import RunningBench
from tests.utilities import bench_checks
from tests.utilities.bench import firmware_versions, open_bench

Recorder = Callable[..., None]


@pytest.fixture(name="bench")
def fixture_bench(
    running_bench: RunningBench,
) -> Iterator[tuple[SignalGenerator, SpectrumAnalyzer]]:
    """Open reset driver sessions on the in-process generator and analyzer."""
    with open_bench(
        "127.0.0.1", running_bench.generator.scpi_port, running_bench.analyzer.scpi_port
    ) as bench:
        yield bench


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


EXPECTED_LEVEL_DBM = expected_level_dbm(
    bench_checks.CARRIER_LEVEL_DBM, bench_checks.CARRIER_FREQUENCY_HZ
)
"""What the analyzer should read for the carrier, from the exact physics model."""


def test_drivers_identify_both_instruments(bench: tuple[SignalGenerator, SpectrumAnalyzer]) -> None:
    """The drivers read each instrument's model and firmware version."""
    generator, analyzer = bench
    assert (generator.identity().model, analyzer.identity().model) == ("SG-1", "SA-1")
    assert firmware_versions(generator, analyzer) == {"generator": "9.9.9", "analyzer": "9.9.9"}


def test_level_and_frequency_at_1ghz(
    bench: tuple[SignalGenerator, SpectrumAnalyzer], record_measurements: Recorder
) -> None:
    """The analyzer reads -10 dBm minus cable loss within 0.5 dB, at 1 GHz within 1 kHz."""
    record_measurements(*bench_checks.carrier_level_and_frequency(*bench, EXPECTED_LEVEL_DBM))


def test_channel_power_at_1ghz(
    bench: tuple[SignalGenerator, SpectrumAnalyzer], record_measurements: Recorder
) -> None:
    """Channel power around the carrier matches the carrier level within 0.5 dB."""
    record_measurements(bench_checks.carrier_channel_power(*bench, EXPECTED_LEVEL_DBM))


def test_noise_floor_with_rf_off(
    bench: tuple[SignalGenerator, SpectrumAnalyzer], record_measurements: Recorder
) -> None:
    """With RF off, the analyzer's peak stays below -110 dBm at 1 kHz RBW."""
    record_measurements(bench_checks.noise_floor_with_rf_off(*bench))


def test_out_of_range_level_is_reported_and_the_session_recovers(
    bench: tuple[SignalGenerator, SpectrumAnalyzer],
) -> None:
    """A level above +20 dBm raises the instrument's -222, and the session still works after."""
    bench_checks.check_out_of_range_level_is_rejected(bench[0])


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
