"""Running the signal generator: settings, startup and the command-line entry point."""

import argparse
import asyncio
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from simulators.common.admin import serve_admin
from simulators.common.app import (
    SimulatorSettings,
    add_common_arguments,
    parse_common_settings,
    serve_until_stopped,
)
from simulators.common.logs import configure_logging
from simulators.common.server import bound_port, serve_lines
from simulators.signal_generator.admin import generator_admin_commands
from simulators.signal_generator.instrument import SignalGeneratorScpi

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings(SimulatorSettings):
    """How the generator runs: the common settings plus its admin port.

    Attributes:
        admin_port: The admin port; 0 picks a free port.
    """

    admin_port: int


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
    add_common_arguments(parser, default_serial="100001")
    parser.add_argument("--admin-port", type=int, default=5125, help="admin port (0 = any free)")
    parsed = parser.parse_args(arguments)
    common = parse_common_settings(parser, parsed, os.environ if environ is None else environ)
    return Settings(
        **asdict(common),
        admin_port=parsed.admin_port,
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
    lock = asyncio.Lock()
    scpi = await serve_lines(
        generator.process, settings.host, settings.scpi_port, name="scpi", lock=lock
    )
    try:
        admin = await serve_admin(
            generator_admin_commands(generator), settings.host, settings.admin_port, lock=lock
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
    await serve_until_stopped(*await start(settings))


def main(arguments: Sequence[str] | None = None) -> None:
    """Run the generator from the command line, logging JSON to stderr.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
    """
    configure_logging()
    asyncio.run(serve_forever(parse_settings(arguments)))
