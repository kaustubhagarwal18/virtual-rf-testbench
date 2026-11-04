"""The admin port: out-of-band access to a simulator, kept apart from its SCPI interface.

Each admin command is one line, a name followed by any arguments, and gets one line of JSON
back. The admin port reports a simulator's state and switches its faults. Test code never
needs it: only the other simulators and the fault tooling use it, so the SCPI interface stays
exactly what a real instrument offers.
"""

import asyncio
import logging
from collections.abc import Callable

from simulators.common.jsontext import to_json
from simulators.common.server import serve_lines

AdminReply = dict[str, object]
"""The JSON object an admin command returns."""

AdminHandler = Callable[[tuple[str, ...]], AdminReply]
"""Runs one admin command with the words after its name, and returns its reply."""

_LOGGER = logging.getLogger(__name__)


class AdminCommandError(Exception):
    """Raised by an admin handler to refuse a command; its message becomes the error reply."""


def expect_no_arguments(name: str, arguments: tuple[str, ...]) -> None:
    """Refuse arguments for a command that takes none.

    Args:
        name: The command, for the error message.
        arguments: The words after the command's name.

    Raises:
        AdminCommandError: If there are any.
    """
    if arguments:
        raise AdminCommandError(f"{name} takes no arguments")


class AdminCommands:
    """A table of admin commands, matched by name regardless of case."""

    def __init__(self) -> None:
        """Start with no commands."""
        self._handlers: dict[str, AdminHandler] = {}

    def register(self, name: str, handler: AdminHandler) -> None:
        """Add a command.

        Args:
            name: The command, for example 'STATE?'.
            handler: Runs the command and returns its reply.
        """
        self._handlers[name.upper()] = handler

    def handle(self, line: str) -> AdminReply:
        """Run one received admin line.

        Args:
            line: The received line.

        Returns:
            The command's reply, or an object with an 'error' key for an empty line, an
            unknown command, one its handler refused, or one that failed; an unexpected
            failure is also logged with its traceback.
        """
        words = line.split()
        if not words:
            return {"error": "Empty admin command"}
        name, arguments = words[0].upper(), tuple(words[1:])
        handler = self._handlers.get(name)
        if handler is None:
            return {"error": f"Unknown admin command: {words[0]}"}
        try:
            return handler(arguments)
        except AdminCommandError as refused:
            return {"error": str(refused)}
        # A failing admin command must still answer with JSON; it is logged instead of raised.
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Admin command failed", extra={"admin_command": name})
            return {"error": f"Internal error in admin command: {name}"}


async def serve_admin(
    commands: AdminCommands, host: str, port: int, *, lock: asyncio.Lock | None = None
) -> asyncio.Server:
    """Start an admin server that answers each line with one line of JSON.

    Connections are logged at debug level: the analyzer connects for every measurement.

    Args:
        commands: The admin commands to offer.
        host: The address to listen on.
        port: The port to listen on; 0 picks a free port.
        lock: The instrument's lock, shared with its SCPI server; None gives the admin
            server its own.

    Returns:
        The started server.
    """

    def handle_line(line: str) -> list[str]:
        """Answer one admin line with its JSON reply.

        Args:
            line: The received line.

        Returns:
            One JSON line.
        """
        return [to_json(commands.handle(line))]

    return await serve_lines(
        handle_line, host, port, name="admin", lock=lock, connection_log_level=logging.DEBUG
    )
