"""The signal generator's admin commands."""

from dataclasses import asdict

from simulators.common.admin import AdminCommands, AdminReply, expect_no_arguments
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

    def state(arguments: tuple[str, ...]) -> AdminReply:
        """Report the generator's frequency, level and RF output.

        Args:
            arguments: The words after STATE?; there must be none.

        Returns:
            The source state.
        """
        expect_no_arguments("STATE?", arguments)
        return asdict(generator.source_state())

    commands.register("STATE?", state)
    return commands
