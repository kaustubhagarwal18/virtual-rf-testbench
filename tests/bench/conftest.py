"""Fixtures for the bench suite, which treats the containerised instruments as black boxes.

Addresses come from BENCH_HOST, BENCH_GENERATOR_PORT and BENCH_ANALYZER_PORT, defaulting to
what make bench-up publishes. If either instrument isn't answering, the suite stops at once.
Every run writes a JSON results file (tests/utilities/results_file.py) to BENCH_RESULTS_JSON,
or reports/bench-results.json.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from drivers.signal_generator import SignalGenerator
from drivers.spectrum_analyzer import SpectrumAnalyzer
from simulators.healthcheck import check_instrument
from tests.utilities.bench import open_bench
from tests.utilities.bench_config import load_bench_config
from tests.utilities.calibration import Calibration, measure_path_loss
from tests.utilities.injected_defects import reset_on_open
from tests.utilities.results_file import (
    DEFAULT_RESULTS_PATH,
    RESULTS_PATH_VARIABLE,
    ResultsCollector,
    ResultsFilePlugin,
    firmware_version_from_identity,
)
from tests.utilities.sequencer import Sequencer

BENCH_HOST = os.environ.get("BENCH_HOST", "127.0.0.1")
GENERATOR_PORT = int(os.environ.get("BENCH_GENERATOR_PORT", "5025"))
ANALYZER_PORT = int(os.environ.get("BENCH_ANALYZER_PORT", "5026"))
BENCH_NOT_RUNNING_EXIT_CODE = 3
ALIVE_TIMEOUT_S = 10.0
"""How long the start-up check waits for *IDN?. Generous on purpose: it asks whether the
instruments are running at all; a slow instrument should fail the tests' own timeouts."""


def pytest_configure(config: pytest.Config) -> None:
    """Start collecting this session's results for the JSON results file.

    Args:
        config: pytest's configuration.
    """
    path = Path(os.environ.get(RESULTS_PATH_VARIABLE, str(DEFAULT_RESULTS_PATH)))
    collector = ResultsCollector(["pytest", *config.invocation_params.args])
    config.pluginmanager.register(ResultsFilePlugin(collector, path), ResultsFilePlugin.name)


@pytest.fixture(scope="session", autouse=True)
def bench_is_running(request: pytest.FixtureRequest) -> None:
    """Stop the run with one clear message if either instrument isn't answering.

    Each instrument's firmware version, from its *IDN? reply, goes into the results file.
    """
    plugin = request.config.pluginmanager.get_plugin(ResultsFilePlugin.name)
    instruments = (
        ("generator", "signal generator", GENERATOR_PORT),
        ("analyzer", "spectrum analyzer", ANALYZER_PORT),
    )
    for role, name, port in instruments:
        health = check_instrument(BENCH_HOST, port, timeout_s=ALIVE_TIMEOUT_S)
        if isinstance(plugin, ResultsFilePlugin) and health.healthy:
            plugin.collector.firmware_versions[role] = firmware_version_from_identity(health.detail)
        if not health.healthy:
            pytest.exit(
                f"The bench isn't running ({name}: {health.detail}). Start it with make bench-up.",
                returncode=BENCH_NOT_RUNNING_EXIT_CODE,
            )


@pytest.fixture(scope="session")
def calibration() -> Calibration:
    """Measure the bench's path loss once, before any level measurement is judged.

    Returns:
        The calibration, whose result every level measurement is then compared with.
    """
    with open_bench(BENCH_HOST, GENERATOR_PORT, ANALYZER_PORT) as (generator, analyzer):
        return measure_path_loss(generator, analyzer)


@pytest.fixture
def generator_address() -> tuple[str, int]:
    """Return where the generator's SCPI port is, for tests that open their own socket.

    Returns:
        The host and port.
    """
    return BENCH_HOST, GENERATOR_PORT


@pytest.fixture(name="sequencer", scope="session")
def fixture_sequencer() -> Sequencer:
    """Open the sequence log every test's setup sequence writes to.

    Returns:
        The sequencer, shared by the whole run so its step numbers are continuous.
    """
    return Sequencer()


def prepare(generator: SignalGenerator, analyzer: SpectrumAnalyzer, sequence: Sequencer) -> None:
    """Put the instruments into the state the bench configuration asks for, step by step.

    These are the steps a station's sequencer runs before it measures anything: read the
    configuration, check the generator carries no stale offset, and set the analyzer's
    channel bandwidth. A step that fails names only itself; its reason goes to the log.

    Args:
        generator: The generator driver.
        analyzer: The analyzer driver.
        sequence: Writes the sequence log.
    """
    with sequence.step("read bench configuration", "station"):
        config = load_bench_config()
    with sequence.step(
        "verify level offset",
        "generator",
        parameter="level_offset_db",
        expected=config.level_offset_db,
    ):
        offset_db = generator.offset_db()
        if offset_db != config.level_offset_db:
            raise AssertionError(
                f"the generator carries a level offset of {offset_db:g} dB, but the bench "
                f"configuration expects {config.level_offset_db:g} dB"
            )
    with sequence.step(
        "set channel bandwidth",
        "analyzer",
        parameter="channel_bandwidth_hz",
        value=config.channel_bandwidth_hz,
    ):
        analyzer.set_channel_bandwidth_hz(config.channel_bandwidth_hz)


@pytest.fixture(name="bench")
def fixture_bench(sequencer: Sequencer) -> Iterator[tuple[SignalGenerator, SpectrumAnalyzer]]:
    """Open reset sessions on both containerised instruments and run the setup sequence.

    The reset is skipped only when the fault tooling switches on the skip-reset defect.
    """
    with open_bench(BENCH_HOST, GENERATOR_PORT, ANALYZER_PORT, reset=reset_on_open()) as (
        generator,
        analyzer,
    ):
        prepare(generator, analyzer, sequencer)
        yield generator, analyzer
