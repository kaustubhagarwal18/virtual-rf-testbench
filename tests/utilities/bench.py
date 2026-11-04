"""Steps both bench suites share: opening the two instruments and taking a peak reading."""

from collections.abc import Iterator
from contextlib import contextmanager

from drivers.session import RsInstrumentSession, socket_resource
from drivers.signal_generator import SignalGenerator
from drivers.spectrum_analyzer import SpectrumAnalyzer
from drivers.types import PeakReading


@contextmanager
def open_bench(
    host: str, generator_port: int, analyzer_port: int
) -> Iterator[tuple[SignalGenerator, SpectrumAnalyzer]]:
    """Open reset driver sessions on both instruments, and close them afterwards.

    Opening a session sends *RST, so every test starts from the instruments' defaults.

    Args:
        host: Where both instruments listen.
        generator_port: The generator's SCPI port.
        analyzer_port: The analyzer's SCPI port.

    Yields:
        The generator and analyzer drivers.
    """
    with (
        RsInstrumentSession(socket_resource(host, generator_port)) as generator_session,
        RsInstrumentSession(socket_resource(host, analyzer_port)) as analyzer_session,
    ):
        yield SignalGenerator(generator_session), SpectrumAnalyzer(analyzer_session)


def firmware_versions(generator: SignalGenerator, analyzer: SpectrumAnalyzer) -> dict[str, str]:
    """Return both instruments' firmware versions, for measurement records.

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


def measure_peak_at(
    generator: SignalGenerator,
    analyzer: SpectrumAnalyzer,
    frequency_hz: float,
    level_dbm: float,
) -> PeakReading:
    """Send a carrier and measure its peak with a 1 MHz span and 1 kHz RBW.

    Args:
        generator: The generator driver.
        analyzer: The analyzer driver.
        frequency_hz: The carrier frequency.
        level_dbm: The generator level.

    Returns:
        The analyzer's peak reading.
    """
    generator.configure(frequency_hz=frequency_hz, level_dbm=level_dbm)
    generator.set_output(on=True)
    analyzer.configure(centre_hz=frequency_hz, span_hz=1e6, rbw_hz=1e3)
    return analyzer.measure_peak()
