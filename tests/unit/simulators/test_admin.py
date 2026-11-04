"""Tests for the admin port: out-of-band JSON access to a simulator."""

import asyncio
import json
import math

import pytest

from simulators.common.admin import AdminCommandError, AdminCommands, serve_admin
from simulators.common.server import bound_port
from simulators.signal_generator.admin import generator_admin_commands
from simulators.signal_generator.instrument import SignalGeneratorScpi
from tests.utilities.strict_json import strict_loads
from tests.utilities.tcp import ask_line


def test_unknown_admin_command_is_an_error_object() -> None:
    """An unknown admin command gets a JSON error, never an exception."""
    assert AdminCommands().handle("FOO") == {"error": "Unknown admin command: FOO"}


def test_registered_command_runs_case_insensitively() -> None:
    """Admin commands match regardless of case and surrounding spaces."""
    commands = AdminCommands()
    commands.register("PING", lambda _arguments: {"pong": True})
    assert commands.handle("  ping ") == {"pong": True}


def test_generator_state_reflects_scpi_settings() -> None:
    """STATE? reports what the generator's SCPI settings say it is sending."""
    generator = SignalGeneratorScpi(firmware_version="1.2.3")
    commands = generator_admin_commands(generator)
    generator.process("FREQ 2 GHz;POW -10;OUTP ON")
    assert commands.handle("STATE?") == {
        "frequency_hz": 2e9,
        "level_dbm": -10.0,
        "output_on": True,
    }


def ask_admin(commands: AdminCommands, line: str) -> str:
    """Serve admin commands on a free local port, send one line and return the reply line.

    Args:
        commands: The admin commands to serve.
        line: The line to send, without its line ending.

    Returns:
        The reply line.
    """

    async def scenario() -> str:
        """Serve the commands, ask one line, then wait for the connection to close."""
        server = await serve_admin(commands, "127.0.0.1", 0)
        async with server:
            reply = await ask_line(bound_port(server), line)
        await asyncio.sleep(0.05)
        return reply

    return asyncio.run(scenario())


def test_admin_server_replies_with_one_json_line() -> None:
    """Over TCP, each admin command gets one line of JSON back."""
    generator = SignalGeneratorScpi(firmware_version="1.2.3")
    assert json.loads(ask_admin(generator_admin_commands(generator), "STATE?")) == {
        "frequency_hz": 1e9,
        "level_dbm": -30.0,
        "output_on": False,
    }


def test_admin_replies_stay_standard_json_with_non_finite_numbers() -> None:
    """NaN or infinity in a reply is sent as a string, never as JSON's missing NaN literal."""
    commands = AdminCommands()
    commands.register("ODD?", lambda _arguments: {"level_dbm": math.nan, "margin_db": math.inf})
    reply = ask_admin(commands, "ODD?")
    assert "NaN" in reply
    assert strict_loads(reply) == {
        "level_dbm": "NaN",
        "margin_db": "Infinity",
    }


def test_failing_admin_command_returns_an_error_object(caplog: pytest.LogCaptureFixture) -> None:
    """An admin command that raises answers with a JSON error and logs the traceback."""
    commands = AdminCommands()

    def broken(arguments: tuple[str, ...]) -> dict[str, object]:
        """Fail the way a programming error would."""
        del arguments
        raise KeyError("missing")

    commands.register("BROKEN?", broken)
    assert commands.handle("BROKEN?") == {"error": "Internal error in admin command: BROKEN?"}
    assert any(record.exc_info for record in caplog.records)


def test_admin_connections_are_logged_at_debug_level(caplog: pytest.LogCaptureFixture) -> None:
    """The analyzer connects for every measurement, so admin connections stay out of INFO logs."""
    caplog.set_level("INFO")
    ask_admin(generator_admin_commands(SignalGeneratorScpi(firmware_version="1.2.3")), "STATE?")
    assert not [record for record in caplog.records if record.getMessage().startswith("Client ")]


def test_arguments_after_the_name_reach_the_handler() -> None:
    """Words after the command name are passed to its handler, case and all."""
    commands = AdminCommands()
    commands.register("ECHO", lambda arguments: {"arguments": list(arguments)})
    assert commands.handle("echo Cable 1.5") == {"arguments": ["Cable", "1.5"]}


def test_a_refused_admin_command_explains_why_without_a_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A handler's AdminCommandError becomes the error reply, and isn't logged as a bug."""
    commands = AdminCommands()

    def refuse(arguments: tuple[str, ...]) -> dict[str, object]:
        """Refuse every call, as a handler does with bad arguments."""
        raise AdminCommandError(f"bad arguments: {arguments}")

    commands.register("REFUSE", refuse)
    assert commands.handle("REFUSE x") == {"error": "bad arguments: ('x',)"}
    assert not [record for record in caplog.records if record.exc_info]


def test_an_empty_admin_line_is_an_error() -> None:
    """A blank line gets an error object, not an exception."""
    assert AdminCommands().handle("   ") == {"error": "Empty admin command"}


def test_state_takes_no_arguments() -> None:
    """STATE? with arguments is refused, so a typo can't pass silently."""
    commands = generator_admin_commands(SignalGeneratorScpi(firmware_version="1.2.3"))
    assert commands.handle("STATE? now") == {"error": "STATE? takes no arguments"}
