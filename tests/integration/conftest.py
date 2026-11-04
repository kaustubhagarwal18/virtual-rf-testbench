"""Fixtures that run a simulator in a background thread for blocking clients like RsInstrument."""

import asyncio
import threading
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from simulators.common.server import bound_port
from simulators.signal_generator.app import Settings, start

STARTUP_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class RunningSimulator:
    """Where a simulator started by a fixture is listening.

    Attributes:
        scpi_port: The SCPI port.
        admin_port: The admin port.
    """

    scpi_port: int
    admin_port: int


async def _close(servers: tuple[asyncio.Server, ...]) -> None:
    """Close servers and wait until they have stopped.

    Args:
        servers: The servers to close.
    """
    for server in servers:
        server.close()
    for server in servers:
        await server.wait_closed()


@pytest.fixture
def running_generator() -> Iterator[RunningSimulator]:
    """Run a signal generator on free local ports in a background event loop."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    settings = Settings(
        host="127.0.0.1",
        scpi_port=0,
        admin_port=0,
        firmware_version="9.9.9",
        serial_number="100001",
    )
    servers = asyncio.run_coroutine_threadsafe(start(settings), loop).result(STARTUP_TIMEOUT_S)
    try:
        yield RunningSimulator(bound_port(servers[0]), bound_port(servers[1]))
    finally:
        asyncio.run_coroutine_threadsafe(_close(servers), loop).result(STARTUP_TIMEOUT_S)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(STARTUP_TIMEOUT_S)
        loop.close()
