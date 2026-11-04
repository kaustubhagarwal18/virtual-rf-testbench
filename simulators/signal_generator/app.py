"""Running the signal generator: settings, startup and the command-line entry point."""

import argparse
import asyncio
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from simulators.common.admin import serve_admin
from simulators.common.logs import configure_logging
from simulators.common.server import bound_port, serve_lines
from simulators.signal_generator.admin import generator_admin_commands
from simulators.signal_generator.instrument import SignalGeneratorScpi

FIRMWARE_VERSION_VARIABLE = "SIMULATOR_FIRMWARE_VERSION"
"""Environment variable that sets the reported firmware version, set when an image is built."""

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    """How the generator runs.

    Attributes:
        host: The address both servers listen on.
        scpi_port: The SCPI port; 0 picks a free port.
        admin_port: The admin port; 0 picks a free port.
        firmware_version: The release reported by *IDN?.
        serial_number: The serial number reported by *IDN?.
    """

    host: str
    scpi_port: int
    admin_port: int
    firmware_version: str
    serial_number: str


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
        prog="python -m simulators.signal_generator",
        description="Simulated VirtualRF SG-1 signal generator",
    )
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on")
    parser.add_argument("--scpi-port", type=int, default=5025, help="SCPI port (0 = any free)")
    parser.add_argument("--admin-port", type=int, default=5125, help="admin port (0 = any free)")
    parser.add_argument("--serial", default="100001", help="serial number reported by *IDN?")
    parsed = parser.parse_args(arguments)
    environment = os.environ if environ is None else environ
    firmware_version = environment.get(FIRMWARE_VERSION_VARIABLE)
    if not firmware_version:
        try:
            firmware_version = version("virtual-rf-testbench")
        except PackageNotFoundError:
            parser.error(
                f"can't tell the firmware version: set {FIRMWARE_VERSION_VARIABLE}, "
                "or install the project with make setup"
            )
    return Settings(
        host=parsed.host,
        scpi_port=parsed.scpi_port,
        admin_port=parsed.admin_port,
        firmware_version=firmware_version,
        serial_number=parsed.serial,
    )


async def start(settings: Settings) -> tuple[asyncio.Server, asyncio.Server]:
    """Start the generator's SCPI and admin servers, sharing one instrument.

    Args:
        settings: How to run.

    Returns:
        The SCPI server and the admin server, both listening.

    Raises:
        OSError: If a port can't be opened; the SCPI server is closed again first, so a
            busy admin port doesn't leave the SCPI port taken.
    """
    generator = SignalGeneratorScpi(settings.firmware_version, settings.serial_number)
    scpi = await serve_lines(generator.process, settings.host, settings.scpi_port, name="scpi")
    try:
        admin = await serve_admin(
            generator_admin_commands(generator), settings.host, settings.admin_port
        )
    except OSError:
        scpi.close()
        await scpi.wait_closed()
        raise
    _LOGGER.info(
        "Signal generator listening",
        extra={
            "host": settings.host,
            "scpi_port": bound_port(scpi),
            "admin_port": bound_port(admin),
            "firmware_version": settings.firmware_version,
        },
    )
    return scpi, admin


async def serve_forever(settings: Settings) -> None:
    """Run the generator until the process is stopped.

    Args:
        settings: How to run.
    """
    scpi, admin = await start(settings)
    async with scpi, admin:
        await asyncio.gather(scpi.serve_forever(), admin.serve_forever())


def main(arguments: Sequence[str] | None = None) -> None:
    """Run the generator from the command line, logging JSON to stderr.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
    """
    configure_logging()
    asyncio.run(serve_forever(parse_settings(arguments)))
