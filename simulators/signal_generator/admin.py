"""The signal generator's admin commands."""

from dataclasses import asdict

from simulators.common.admin import AdminCommands
from simulators.signal_generator.instrument import SignalGeneratorScpi


def generator_admin_commands(generator: SignalGeneratorScpi) -> AdminCommands:
    """Build the admin commands for a generator.

    Args:
        generator: The generator the commands act on.

    Returns:
        The admin commands: STATE? reports frequency, level and RF output as the analyzer
        reads them (see ADR 0002).
    """
    commands = AdminCommands()
    commands.register("STATE?", lambda: asdict(generator.source_state()))
    return commands
