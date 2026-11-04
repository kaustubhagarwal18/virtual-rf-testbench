"""Startup pieces every simulator shares: settings, arguments, version, running servers."""

import argparse
import asyncio
import contextlib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

FIRMWARE_VERSION_VARIABLE = "SIMULATOR_FIRMWARE_VERSION"
"""Environment variable that sets the reported firmware version, set when an image is built."""

PACKAGE_NAME = "virtual-rf-testbench"


MISSING_VERSION_MESSAGE = (
    f"can't tell the firmware version: set {FIRMWARE_VERSION_VARIABLE}, "
    "or install the project with make setup"
)
"""What a simulator reports when firmware_version() can't find a version."""


def firmware_version(environ: Mapping[str, str]) -> str | None:
    """Return the firmware version a simulator reports in *IDN?.

    Args:
        environ: The environment to read SIMULATOR_FIRMWARE_VERSION from.

    Returns:
        SIMULATOR_FIRMWARE_VERSION if set, otherwise the installed package version, or None
        when neither is known; the caller then stops with MISSING_VERSION_MESSAGE.
    """
    configured = environ.get(FIRMWARE_VERSION_VARIABLE)
    if configured:
        return configured
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return None


@dataclass(frozen=True)
class SimulatorSettings:
    """What every simulator needs to run; each simulator's Settings adds its own fields.

    Attributes:
        host: The address its servers listen on.
        scpi_port: The SCPI port; 0 picks a free port.
        firmware_version: The release reported by *IDN?.
        serial_number: The serial number reported by *IDN?.
    """

    host: str
    scpi_port: int
    firmware_version: str
    serial_number: str


def add_common_arguments(parser: argparse.ArgumentParser, default_serial: str) -> None:
    """Add the arguments every simulator takes: --host, --scpi-port and --serial.

    Args:
        parser: The simulator's argument parser.
        default_serial: The serial number *IDN? reports when --serial isn't given.
    """
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on")
    parser.add_argument("--scpi-port", type=int, default=5025, help="SCPI port (0 = any free)")
    parser.add_argument("--serial", default=default_serial, help="serial number reported by *IDN?")


def require_firmware_version(parser: argparse.ArgumentParser, environ: Mapping[str, str]) -> str:
    """Return the firmware version, or stop with a one-line message saying what to set.

    Args:
        parser: The simulator's argument parser, which reports the problem and exits.
        environ: The environment to read SIMULATOR_FIRMWARE_VERSION from.

    Returns:
        The firmware version.
    """
    reported_version = firmware_version(environ)
    if reported_version is None:
        parser.error(MISSING_VERSION_MESSAGE)
    return reported_version


def parse_common_settings(
    parser: argparse.ArgumentParser, parsed: argparse.Namespace, environ: Mapping[str, str]
) -> SimulatorSettings:
    """Build the settings every simulator shares from its parsed arguments.

    Args:
        parser: The simulator's argument parser, which reports a missing version and exits.
        parsed: The parsed command-line arguments.
        environ: The environment to read SIMULATOR_FIRMWARE_VERSION from.

    Returns:
        The common settings.
    """
    return SimulatorSettings(
        host=parsed.host,
        scpi_port=parsed.scpi_port,
        firmware_version=require_firmware_version(parser, environ),
        serial_number=parsed.serial,
    )


async def serve_until_stopped(*servers: asyncio.Server) -> None:
    """Serve on every server until the process is stopped, then close them all.

    Args:
        *servers: The started servers.
    """
    async with contextlib.AsyncExitStack() as stack:
        for server in servers:
            await stack.enter_async_context(server)
        await asyncio.gather(*(server.serve_forever() for server in servers))
