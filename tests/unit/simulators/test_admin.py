"""Tests for the admin port: out-of-band JSON access to a simulator."""

import asyncio
import json
import math

import pytest

from simulators.common.admin import AdminCommands, serve_admin
from simulators.common.server import bound_port
from simulators.signal_generator.admin import generator_admin_commands
from simulators.signal_generator.instrument import SignalGeneratorScpi
from tests.support.strict_json import strict_loads
from tests.support.tcp import ask_line


def test_unknown_admin_command_is_an_error_object() -> None:
    """An unknown admin command gets a JSON error, never an exception."""
    assert AdminCommands().handle("FOO") == {"error": "Unknown admin command: FOO"}


def test_registered_command_runs_case_insensitively() -> None:
    """Admin commands match regardless of case and surrounding spaces."""
    commands = AdminCommands()
    commands.register("PING", lambda: {"pong": True})
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
    commands.register("ODD?", lambda: {"level_dbm": math.nan, "margin_db": math.inf})
    reply = ask_admin(commands, "ODD?")
    assert "NaN" in reply
    assert strict_loads(reply) == {
        "level_dbm": "NaN",
        "margin_db": "Infinity",
    }


def test_failing_admin_command_returns_an_error_object(caplog: pytest.LogCaptureFixture) -> None:
    """An admin command that raises answers with a JSON error and logs the traceback."""
    commands = AdminCommands()

    def broken() -> dict[str, object]:
        """Fail the way a programming error would."""
        raise KeyError("missing")

    commands.register("BROKEN?", broken)
    assert commands.handle("BROKEN?") == {"error": "Internal error in admin command: BROKEN?"}
    assert any(record.exc_info for record in caplog.records)


def test_admin_connections_are_logged_at_debug_level(caplog: pytest.LogCaptureFixture) -> None:
    """The analyzer connects for every measurement, so admin connections stay out of INFO logs."""
    caplog.set_level("INFO")
    ask_admin(generator_admin_commands(SignalGeneratorScpi(firmware_version="1.2.3")), "STATE?")
    assert not [record for record in caplog.records if record.getMessage().startswith("Client ")]
