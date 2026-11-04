"""Fixtures for the bench suite, which treats the containerised instruments as black boxes.

Addresses come from BENCH_HOST, BENCH_GENERATOR_PORT and BENCH_ANALYZER_PORT, defaulting to
what make bench-up publishes. If either instrument isn't answering, the suite stops at once.
"""

import os
from collections.abc import Iterator

import pytest

from drivers.signal_generator import SignalGenerator
from drivers.spectrum_analyzer import SpectrumAnalyzer
from simulators.healthcheck import check_instrument
from tests.utilities.bench import open_bench

BENCH_HOST = os.environ.get("BENCH_HOST", "127.0.0.1")
GENERATOR_PORT = int(os.environ.get("BENCH_GENERATOR_PORT", "5025"))
ANALYZER_PORT = int(os.environ.get("BENCH_ANALYZER_PORT", "5026"))
BENCH_NOT_RUNNING_EXIT_CODE = 3


@pytest.fixture(scope="session", autouse=True)
def bench_is_running() -> None:
    """Stop the run with one clear message if either instrument isn't answering."""
    for name, port in (("signal generator", GENERATOR_PORT), ("spectrum analyzer", ANALYZER_PORT)):
        health = check_instrument(BENCH_HOST, port, timeout_s=2.0)
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
