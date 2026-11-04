"""Tests for the integration test helpers themselves: they must fail clearly, never hang."""

import asyncio
from typing import cast

import pytest

from tests.integration.conftest import ServersInBackground
from tests.integration.processes import simulator_process


def test_crashing_simulator_reports_its_exit_code_and_stderr() -> None:
    """A simulator that exits before it listens fails the test with its own error message."""
    with (
        pytest.raises(RuntimeError, match=r"(?s)exited with code 2.*invalid int value"),
        simulator_process(
            "simulators.signal_generator",
            ["--scpi-port", "not-a-port"],
            firmware_version="1.0.0",
            ready_message="Signal generator listening",
        ),
    ):
        pass


def test_simulator_that_never_gets_ready_times_out() -> None:
    """Waiting for a log line that never comes fails after the startup timeout."""
    with (
        pytest.raises(RuntimeError, match=r"didn't log 'never' within 1\.0 s"),
        simulator_process(
            "simulators.signal_generator",
            ["--scpi-port", "0", "--admin-port", "0"],
            firmware_version="1.0.0",
            ready_message="never",
            startup_timeout_s=1.0,
        ),
    ):
        pass


def test_failed_background_start_leaves_no_thread_running() -> None:
    """If starting servers fails, the background loop and its thread are stopped too."""

    async def failing_start() -> tuple[asyncio.Server, ...]:
        """Fail the way a busy port would.

        Raises:
            OSError: Always.
        """
        raise OSError("address already in use")

    background = ServersInBackground(failing_start)
    with pytest.raises(OSError, match="already in use"), background:
        pass
    assert not background.thread.is_alive()


class _UnclosableServer:
    """Stands in for a server whose close() fails."""

    def close(self) -> None:
        """Fail the way a stuck shutdown would.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("close failed")

    async def wait_closed(self) -> None:
        """Never reached, because close() fails first."""


def test_failed_close_still_stops_the_background_thread() -> None:
    """If closing the servers fails, the background loop and its thread are stopped anyway."""

    async def start_unclosable() -> tuple[asyncio.Server, ...]:
        """Return a server that can't be closed.

        Returns:
            The stand-in server.
        """
        return (cast(asyncio.Server, _UnclosableServer()),)

    background = ServersInBackground(start_unclosable)
    with pytest.raises(RuntimeError, match="close failed"), background:
        pass
    assert not background.thread.is_alive()
