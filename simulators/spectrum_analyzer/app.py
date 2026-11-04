"""Running the spectrum analyzer: settings, startup and the command-line entry point."""

import argparse
import asyncio
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from simulators.common.app import (
    SimulatorSettings,
    add_common_arguments,
    parse_common_settings,
    serve_until_stopped,
)
from simulators.common.logs import json_logging
from simulators.common.physics import BenchParameters, PhysicsModel
from simulators.common.server import bound_port, serve_lines
from simulators.spectrum_analyzer.instrument import SpectrumAnalyzerScpi
from simulators.spectrum_analyzer.link import AdminPortLink

_LOGGER = logging.getLogger(__name__)
_HIGHEST_PORT = 65535


@dataclass(frozen=True)
class Settings(SimulatorSettings):
    """How the analyzer runs: the common settings plus where the generator's admin port is.

    Attributes:
        generator_admin_host: Where the generator's admin port is.
        generator_admin_port: The generator's admin port.
    """

    generator_admin_host: str
    generator_admin_port: int


def _host_and_port(text: str) -> tuple[str, int]:
    """Read a host:port address from the command line.

    Args:
        text: The address, for example 'signal-generator:5125' or '[::1]:5125'.

    Returns:
        The host and the port.

    Raises:
        argparse.ArgumentTypeError: If the text isn't host:port with a port from 1 to 65535.
    """
    host, separator, port_text = text.rpartition(":")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    valid_port = port_text.isdigit() and 1 <= int(port_text) <= _HIGHEST_PORT
    if not separator or not host or not valid_port:
        raise argparse.ArgumentTypeError(f"expected host:port, got {text!r}")
    return host, int(port_text)


def parse_settings(
    arguments: Sequence[str] | None = None, environ: Mapping[str, str] | None = None
) -> Settings:
    """Read the settings from command-line arguments and the environment.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        environ: The environment; None reads os.environ.

    Returns:
        The settings. The firmware version comes from SIMULATOR_FIRMWARE_VERSION, or the
        package version when that isn't set; with neither, argparse exits with a one-line
        message saying what to set.
    """
    parser = argparse.ArgumentParser(
        prog="python -m simulators.spectrum_analyzer",
        description="Simulated VirtualRF SA-1 spectrum analyzer",
    )
    add_common_arguments(parser, default_serial="200001")
    parser.add_argument(
        "--generator-admin",
        type=_host_and_port,
        default=("127.0.0.1", 5125),
        metavar="HOST:PORT",
        help="the signal generator's admin port, which the analyzer measures",
    )
    parsed = parser.parse_args(arguments)
    generator_admin_host, generator_admin_port = parsed.generator_admin
    common = parse_common_settings(parser, parsed, os.environ if environ is None else environ)
    return Settings(
        **asdict(common),
        generator_admin_host=generator_admin_host,
        generator_admin_port=generator_admin_port,
    )


async def start(settings: Settings) -> asyncio.Server:
    """Start the analyzer's SCPI server, measuring the generator at the configured address.

    Args:
        settings: How to run.

    Returns:
        The SCPI server, listening.
    """
    analyzer = SpectrumAnalyzerScpi(
        settings.firmware_version,
        AdminPortLink(settings.generator_admin_host, settings.generator_admin_port),
        PhysicsModel(BenchParameters()),
        settings.serial_number,
    )
    scpi = await serve_lines(analyzer.process, settings.host, settings.scpi_port, name="scpi")
    _LOGGER.info(
        "Spectrum analyzer listening",
        extra={
            "host": settings.host,
            "scpi_port": bound_port(scpi),
            "generator_admin": f"{settings.generator_admin_host}:{settings.generator_admin_port}",
            "firmware_version": settings.firmware_version,
        },
    )
    return scpi


async def serve_forever(settings: Settings) -> None:
    """Run the analyzer until the process is stopped.

    Args:
        settings: How to run.
    """
    await serve_until_stopped(await start(settings))


def main(arguments: Sequence[str] | None = None) -> None:
    """Run the analyzer from the command line, logging JSON to stderr.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
    """
    with json_logging():
        asyncio.run(serve_forever(parse_settings(arguments)))
