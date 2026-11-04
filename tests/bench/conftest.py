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
from tests.utilities.results_file import (
    DEFAULT_RESULTS_PATH,
    RESULTS_PATH_VARIABLE,
    ResultsCollector,
    ResultsFilePlugin,
    firmware_version_from_identity,
)

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


@pytest.fixture(name="bench")
def fixture_bench() -> Iterator[tuple[SignalGenerator, SpectrumAnalyzer]]:
    """Open reset sessions on both containerised instruments for one test."""
    with open_bench(BENCH_HOST, GENERATOR_PORT, ANALYZER_PORT) as bench:
        yield bench
