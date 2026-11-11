"""Startup pieces every simulator shares: settings, arguments, version, running servers."""

import argparse
import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from simulators.common.admin import AdminCommands, serve_admin
from simulators.common.logs import json_logging
from simulators.common.server import LineHandler, serve_lines

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


async def start_scpi_and_admin(
    handler: LineHandler,
    commands: AdminCommands,
    host: str,
    scpi_port: int,
    admin_port: int,
    *,
    scpi_delay_s: Callable[[str], float] | None = None,
) -> tuple[asyncio.Server, asyncio.Server]:
    """Start an instrument's SCPI and admin servers, sharing one lock so they never interleave.

    Args:
        handler: Answers each SCPI line.
        commands: The admin commands.
        host: The address to listen on.
        scpi_port: The SCPI port; 0 picks a free port.
        admin_port: The admin port; 0 picks a free port.
        scpi_delay_s: Asked with each SCPI line, for a delay that doesn't hold the lock;
            None means no delay.

    Returns:
        The SCPI server and the admin server, both listening.

    Raises:
        OSError: If a port can't be opened; the SCPI server is closed again first, so a
            busy admin port doesn't leave the SCPI port taken.
    """
    lock = asyncio.Lock()
    scpi = await serve_lines(handler, host, scpi_port, name="scpi", lock=lock, delay_s=scpi_delay_s)
    try:
        admin = await serve_admin(commands, host, admin_port, lock=lock)
    except OSError:
        scpi.close()
        await scpi.wait_closed()
        raise
    return scpi, admin


async def serve_until_stopped(*servers: asyncio.Server) -> None:
    """Serve on every server until the process is stopped, then close them all.

    Args:
        *servers: The started servers.
    """
    async with contextlib.AsyncExitStack() as stack:
        for server in servers:
            await stack.enter_async_context(server)
        await asyncio.gather(*(server.serve_forever() for server in servers))


def run_simulator[SettingsT](
    arguments: Sequence[str] | None,
    parse_settings: Callable[[Sequence[str] | None], SettingsT],
    start: Callable[[SettingsT], Awaitable[tuple[asyncio.Server, ...]]],
) -> None:
    """Run a simulator from the command line until the process is stopped, logging JSON.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        parse_settings: Reads the simulator's settings from those arguments.
        start: Starts the simulator's servers.
    """

    async def serve(settings: SettingsT) -> None:
        """Start the servers and serve until stopped.

        Args:
            settings: How to run.
        """
        await serve_until_stopped(*await start(settings))

    with json_logging():
        asyncio.run(serve(parse_settings(arguments)))
