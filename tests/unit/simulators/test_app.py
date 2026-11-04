"""Tests for the startup pieces every simulator shares."""

import argparse
import asyncio
from importlib.metadata import PackageNotFoundError, version

import pytest

from simulators.common import app
from simulators.common.app import (
    SimulatorSettings,
    add_common_arguments,
    firmware_version,
    parse_common_settings,
    require_firmware_version,
    serve_until_stopped,
)
from simulators.common.server import serve_lines


def not_installed(_distribution: str) -> str:
    """Behave as if the project isn't installed.

    Raises:
        PackageNotFoundError: Always.
    """
    raise PackageNotFoundError("virtual-rf-testbench")


def test_version_variable_wins() -> None:
    """SIMULATOR_FIRMWARE_VERSION sets the version when it is present."""
    assert firmware_version({"SIMULATOR_FIRMWARE_VERSION": "2.0.0"}) == "2.0.0"


def test_package_version_is_the_fallback() -> None:
    """Without the variable, the installed package version is used."""
    assert firmware_version({}) == version("virtual-rf-testbench")


def test_unknown_version_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """With neither, the version is unknown rather than guessed."""
    monkeypatch.setattr(app, "version", not_installed)
    assert firmware_version({}) is None


def test_require_firmware_version_stops_with_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing version stops the simulator with one line saying what to set."""
    monkeypatch.setattr(app, "version", not_installed)
    with pytest.raises(SystemExit):
        require_firmware_version(argparse.ArgumentParser(prog="sim"), {})
    assert "sim: error: can't tell the firmware version" in capsys.readouterr().err


def test_common_arguments_and_settings() -> None:
    """Every simulator takes --host, --scpi-port and --serial, with local defaults."""
    parser = argparse.ArgumentParser()
    add_common_arguments(parser, default_serial="300003")
    settings = parse_common_settings(
        parser, parser.parse_args([]), {"SIMULATOR_FIRMWARE_VERSION": "1.0.0"}
    )
    assert settings == SimulatorSettings(
        host="127.0.0.1", scpi_port=5025, firmware_version="1.0.0", serial_number="300003"
    )


def test_serve_until_stopped_closes_its_servers() -> None:
    """When serving is cancelled, every server is closed."""

    async def scenario() -> bool:
        """Serve, cancel, and report whether the server is still listening."""
        server = await serve_lines(lambda line: [line], "127.0.0.1", 0, name="test")
        task = asyncio.create_task(serve_until_stopped(server))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return server.is_serving()

    assert asyncio.run(scenario()) is False
