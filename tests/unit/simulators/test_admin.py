"""Tests for the admin port: out-of-band JSON access to a simulator."""

import asyncio
import json

import pytest

from simulators.common.admin import AdminCommands, serve_admin
from simulators.common.server import bound_port
from simulators.signal_generator.admin import generator_admin_commands
from simulators.signal_generator.instrument import SignalGeneratorScpi


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


def test_admin_server_replies_with_one_json_line() -> None:
    """Over TCP, each admin command gets one line of JSON back."""

    async def scenario() -> str:
        """Query the generator's state over the admin port."""
        generator = SignalGeneratorScpi(firmware_version="1.2.3")
        server = await serve_admin(generator_admin_commands(generator), "127.0.0.1", 0)
        async with server:
            reader, writer = await asyncio.open_connection("127.0.0.1", bound_port(server))
            writer.write(b"STATE?\n")
            await writer.drain()
            reply = await asyncio.wait_for(reader.readline(), 5.0)
            writer.close()
            await writer.wait_closed()
        return reply.decode()

    assert json.loads(asyncio.run(scenario())) == {
        "frequency_hz": 1e9,
        "level_dbm": -30.0,
        "output_on": False,
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
