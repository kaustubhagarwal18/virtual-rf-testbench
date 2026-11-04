"""The spectrum analyzer started as its own process, the way Docker will run it."""

from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError

import pytest

from simulators.common import app as common_app
from simulators.spectrum_analyzer.app import parse_settings
from tests.integration.processes import ask, simulator_process


@pytest.fixture(name="listening")
def fixture_listening() -> Iterator[dict[str, object]]:
    """Start the analyzer on a free port and return its 'listening' log entry."""
    with simulator_process(
        "simulators.spectrum_analyzer",
        ["--scpi-port", "0"],
        firmware_version="7.8.9",
        ready_message="Spectrum analyzer listening",
    ) as entry:
        yield entry


def test_process_logs_json_and_answers_on_its_port(listening: dict[str, object]) -> None:
    """The process logs where it listens and which generator it measures, and answers *IDN?."""
    assert listening["generator_admin"] == "127.0.0.1:5125"
    assert ask(listening["scpi_port"], "*IDN?") == "VirtualRF,SA-1,200001,7.8.9"


def test_settings_default_to_the_local_generator() -> None:
    """With no arguments, the analyzer listens on 5025 and measures the generator on 5125."""
    settings = parse_settings([], environ={"SIMULATOR_FIRMWARE_VERSION": "1.0.0"})
    assert (settings.host, settings.scpi_port) == ("127.0.0.1", 5025)
    assert (settings.generator_admin_host, settings.generator_admin_port) == ("127.0.0.1", 5125)
    assert settings.serial_number == "200001"


def test_generator_admin_address_is_host_and_port() -> None:
    """--generator-admin takes host:port, as Docker Compose will pass it."""
    scpi_port = 6026
    settings = parse_settings(
        ["--generator-admin", "signal-generator:6125", "--scpi-port", str(scpi_port)],
        environ={"SIMULATOR_FIRMWARE_VERSION": "1.0.0"},
    )
    assert (settings.generator_admin_host, settings.generator_admin_port) == (
        "signal-generator",
        6125,
    )
    assert settings.scpi_port == scpi_port


@pytest.mark.parametrize(
    "address", ["no-port", "host:", ":5125", "host:abc", "host:0", "host:70000", "[]:5125"]
)
def test_bad_generator_admin_address_is_a_usage_error(
    address: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A generator address that isn't host:port stops with a one-line usage error."""
    with pytest.raises(SystemExit):
        parse_settings(["--generator-admin", address], environ={})
    assert "host:port" in capsys.readouterr().err


def test_missing_version_is_one_clear_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no version known, the analyzer says what to set, like the generator."""

    def not_installed(_distribution: str) -> str:
        """Behave as if the project isn't installed."""
        raise PackageNotFoundError("virtual-rf-testbench")

    monkeypatch.setattr(common_app, "version", not_installed)
    with pytest.raises(SystemExit):
        parse_settings([], environ={})
    assert "SIMULATOR_FIRMWARE_VERSION" in capsys.readouterr().err


def test_ipv6_generator_address_loses_its_brackets() -> None:
    """An IPv6 address in brackets, as URLs write it, is passed on without the brackets."""
    settings = parse_settings(
        ["--generator-admin", "[::1]:5125"], environ={"SIMULATOR_FIRMWARE_VERSION": "1.0.0"}
    )
    assert settings.generator_admin_host == "::1"
