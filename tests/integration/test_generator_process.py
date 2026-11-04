"""The signal generator started as its own process, the way Docker will run it."""

import asyncio
import socket
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError

import pytest

from simulators.common import app as common_app
from simulators.signal_generator.app import Settings, parse_settings, start
from tests.integration.processes import ask, simulator_process

ANY_ADDRESS = "0.0.0.0"
"""The address a container listens on; here it is only parsed, never bound."""


@pytest.fixture(name="listening")
def fixture_listening() -> Iterator[dict[str, object]]:
    """Start the generator on free ports and return its 'listening' log entry."""
    with simulator_process(
        "simulators.signal_generator",
        ["--scpi-port", "0", "--admin-port", "0"],
        firmware_version="4.5.6",
        ready_message="Signal generator listening",
    ) as entry:
        yield entry


def test_process_logs_json_and_answers_on_its_port(listening: dict[str, object]) -> None:
    """The process logs where it listens, as JSON, and answers *IDN? with the configured version."""
    assert ask(listening["scpi_port"], "*IDN?") == "VirtualRF,SG-1,100001,4.5.6"


def test_settings_default_to_local_ports_and_package_version() -> None:
    """With no arguments, the generator listens locally on 5025 and 5125."""
    settings = parse_settings([], environ={})
    assert (settings.host, settings.scpi_port, settings.admin_port) == ("127.0.0.1", 5025, 5125)
    assert settings.firmware_version
    assert settings.serial_number == "100001"


def test_settings_take_arguments_and_the_firmware_version_variable() -> None:
    """Arguments set host, ports and serial; SIMULATOR_FIRMWARE_VERSION sets the version."""
    arguments = ["--host", ANY_ADDRESS, "--scpi-port", "6025", "--admin-port", "6125"]
    settings = parse_settings(
        [*arguments, "--serial", "200002"], environ={"SIMULATOR_FIRMWARE_VERSION": "2.0.0"}
    )
    assert (settings.host, settings.scpi_port, settings.admin_port) == (ANY_ADDRESS, 6025, 6125)
    assert (settings.firmware_version, settings.serial_number) == ("2.0.0", "200002")


def free_port() -> int:
    """Return a local port that is free right now.

    Returns:
        The port number.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def test_busy_admin_port_releases_the_scpi_port() -> None:
    """If the admin port is taken, startup fails and the SCPI port is released again."""
    scpi_port = free_port()
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        settings = Settings(
            host="127.0.0.1",
            scpi_port=scpi_port,
            firmware_version="1.0.0",
            serial_number="100001",
            admin_port=blocker.getsockname()[1],
        )
        with pytest.raises(OSError):
            asyncio.run(start(settings))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", scpi_port))


def test_missing_version_is_one_clear_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no version variable and no installed package, startup says what to set."""

    def not_installed(_distribution: str) -> str:
        """Behave as if the project isn't installed."""
        raise PackageNotFoundError("virtual-rf-testbench")

    monkeypatch.setattr(common_app, "version", not_installed)
    with pytest.raises(SystemExit) as raised:
        parse_settings([], environ={})
    usage_error_exit_code = 2
    assert raised.value.code == usage_error_exit_code
    assert "SIMULATOR_FIRMWARE_VERSION" in capsys.readouterr().err
