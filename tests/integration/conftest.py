"""Fixtures that run simulators in background threads for blocking clients like RsInstrument.

Each simulator gets its own event loop and thread, as each gets its own process in Docker.
Sharing one loop also works (test_same_event_loop.py); separate loops keep these fixtures
closer to how the bench really runs.
"""

import asyncio
import threading
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass

import pytest

from measurements.junit import record_in_junit
from measurements.record import Measurement
from simulators.common.server import bound_port
from simulators.signal_generator import app as generator_app
from simulators.spectrum_analyzer import app as analyzer_app

STARTUP_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class RunningSimulator:
    """Where a simulator started by a fixture is listening.

    Attributes:
        scpi_port: The SCPI port.
        admin_port: The admin port, or 0 if the simulator has none.
    """

    scpi_port: int
    admin_port: int = 0


@dataclass(frozen=True)
class RunningBench:
    """A generator and an analyzer wired together, as on the bench.

    Attributes:
        generator: Where the generator listens.
        analyzer: Where the analyzer listens.
    """

    generator: RunningSimulator
    analyzer: RunningSimulator


async def _close(servers: tuple[asyncio.Server, ...]) -> None:
    """Close servers and wait until they have stopped.

    Args:
        servers: The servers to close.
    """
    for server in servers:
        server.close()
    for server in servers:
        await server.wait_closed()


class ServersInBackground:
    """Starts servers on their own event loop in a background thread, and stops them on exit."""

    def __init__(self, start: Callable[[], Awaitable[tuple[asyncio.Server, ...]]]) -> None:
        """Remember how to start the servers.

        Args:
            start: Starts the servers and returns them.
        """
        self._start = start
        self._loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self.servers: tuple[asyncio.Server, ...] = ()

    def __enter__(self) -> "ServersInBackground":
        """Start the loop thread and the servers on it.

        Returns:
            This object, with its servers listening.
        """
        self.thread.start()

        async def started() -> tuple[asyncio.Server, ...]:
            """Run the start function inside the background loop.

            Returns:
                The started servers.
            """
            return await self._start()

        future = asyncio.run_coroutine_threadsafe(started(), self._loop)
        started_ok = False
        try:
            self.servers = future.result(STARTUP_TIMEOUT_S)
            started_ok = True
        finally:
            if not started_ok:
                self._stop_loop()
        return self

    def __exit__(self, *_exception: object) -> None:
        """Stop the servers, the loop and its thread.

        Args:
            *_exception: The exception that ended the block, if any; it isn't suppressed.
        """
        try:
            future = asyncio.run_coroutine_threadsafe(_close(self.servers), self._loop)
            future.result(STARTUP_TIMEOUT_S)
        finally:
            self._stop_loop()

    def _stop_loop(self) -> None:
        """Stop the background loop, wait for its thread and close the loop."""
        self._loop.call_soon_threadsafe(self._loop.stop)
        self.thread.join(STARTUP_TIMEOUT_S)
        self._loop.close()

    def ports(self) -> list[int]:
        """Return the port of each server, in the order they were started.

        Returns:
            The ports.
        """
        return [bound_port(server) for server in self.servers]


def generator_settings() -> generator_app.Settings:
    """Return generator settings on free local ports.

    Returns:
        The settings.
    """
    return generator_app.Settings(
        host="127.0.0.1",
        scpi_port=0,
        firmware_version="9.9.9",
        serial_number="100001",
        admin_port=0,
    )


@pytest.fixture
def running_generator() -> Iterator[RunningSimulator]:
    """Run a signal generator on free local ports in a background event loop."""
    with ServersInBackground(lambda: generator_app.start(generator_settings())) as generator:
        scpi_port, admin_port = generator.ports()
        yield RunningSimulator(scpi_port, admin_port)


@pytest.fixture
def running_bench() -> Iterator[RunningBench]:
    """Run a generator and an analyzer that measures it, each on its own event loop."""
    with ServersInBackground(lambda: generator_app.start(generator_settings())) as generator:
        generator_scpi_port, generator_admin_port = generator.ports()

        async def start_analyzer() -> tuple[asyncio.Server, ...]:
            """Start the analyzer, linked to the generator's admin port.

            Returns:
                The analyzer's SCPI server.
            """
            settings = analyzer_app.Settings(
                host="127.0.0.1",
                scpi_port=0,
                generator_admin_host="127.0.0.1",
                generator_admin_port=generator_admin_port,
                firmware_version="9.9.9",
                serial_number="200001",
            )
            return (await analyzer_app.start(settings),)

        with ServersInBackground(start_analyzer) as analyzer:
            (analyzer_scpi_port,) = analyzer.ports()
            yield RunningBench(
                RunningSimulator(generator_scpi_port, generator_admin_port),
                RunningSimulator(analyzer_scpi_port),
            )


@pytest.fixture
def record_measurements(
    record_property: Callable[[str, object], None],
) -> Callable[..., None]:
    """Record measurements in the JUnit XML, then fail the test if any is outside its limit.

    Args:
        record_property: pytest's fixture for adding properties to the JUnit XML.

    Returns:
        A function that records one or more measurements.
    """

    def record(*measurements: Measurement) -> None:
        """Record every measurement, then fail listing each one that missed its limit.

        Args:
            *measurements: The measurements to record.
        """
        failed = record_in_junit(record_property, measurements)
        if failed:
            pytest.fail("\n".join(measurement.describe() for measurement in failed))

    return record
