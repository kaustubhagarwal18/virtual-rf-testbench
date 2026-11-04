"""Running the signal generator: settings, startup and the command-line entry point."""

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
    run_simulator,
    start_scpi_and_admin,
)
from simulators.common.faults import SLOW_RESPONSES, FaultSwitches, SlowResponses
from simulators.common.server import bound_port
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

    The admin port reports the generator's state and switches its fault: slow responses.

    Args:
        settings: How to run.

    Returns:
        The SCPI server and the admin server, both listening.
    """
    generator = SignalGeneratorScpi(settings.firmware_version, settings.serial_number)
    switches = FaultSwitches([SLOW_RESPONSES])
    commands = generator_admin_commands(generator)
    switches.register_commands(commands)
    scpi, admin = await start_scpi_and_admin(
        SlowResponses(generator.process, switches),
        commands,
        settings.host,
        settings.scpi_port,
        settings.admin_port,
    )
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


def main(arguments: Sequence[str] | None = None) -> None:
    """Run the generator from the command line, logging JSON to stderr.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
    """
    run_simulator(arguments, parse_settings, start)
