"""Tests for the line server that carries SCPI and admin traffic over TCP."""

import asyncio
import socket
import struct
from types import SimpleNamespace
from typing import cast

import pytest

from simulators.common.server import MAX_LINE_BYTES, bound_port, serve_lines
from simulators.signal_generator.instrument import SignalGeneratorScpi

TIMEOUT_S = 5.0


async def start_generator_server() -> tuple[asyncio.Server, SignalGeneratorScpi]:
    """Start a generator's SCPI server on a free local port."""
    generator = SignalGeneratorScpi(firmware_version="1.2.3")
    server = await serve_lines(generator.process, "127.0.0.1", 0, name="test")
    return server, generator


async def connect(server: asyncio.Server) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a client connection to the server."""
    return await asyncio.open_connection("127.0.0.1", bound_port(server))


async def ask(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, line: str) -> str:
    """Send one line and return the next reply line, without its line ending."""
    writer.write(line.encode())
    await writer.drain()
    return (await asyncio.wait_for(reader.readline(), TIMEOUT_S)).decode().rstrip("\n")


async def close(writer: asyncio.StreamWriter) -> None:
    """Close a client connection."""
    writer.close()
    await writer.wait_closed()


def test_server_answers_a_query() -> None:
    """A query over TCP gets its reply on one line."""

    async def scenario() -> str:
        """Ask the generator to identify itself."""
        server, _ = await start_generator_server()
        async with server:
            reader, writer = await connect(server)
            reply = await ask(reader, writer, "*IDN?\n")
            await close(writer)
        return reply

    assert asyncio.run(scenario()) == "VirtualRF,SG-1,100001,1.2.3"


def test_commands_without_replies_send_nothing() -> None:
    """Setting commands and unknown queries send nothing, so the next reply is the next query's."""

    async def scenario() -> str:
        """Send a setting and an unknown query, then read the error queue."""
        server, _ = await start_generator_server()
        async with server:
            reader, writer = await connect(server)
            reply = await ask(reader, writer, "FREQ 2 GHz\nBOGus?\nSYST:ERR?\n")
            await close(writer)
        return reply

    assert asyncio.run(scenario()) == '-113,"Undefined header"'


def test_windows_line_endings_are_accepted() -> None:
    """A command ending in CR LF works the same as one ending in LF."""

    async def scenario() -> str:
        """Send a query with a CR LF ending."""
        server, _ = await start_generator_server()
        async with server:
            reader, writer = await connect(server)
            reply = await ask(reader, writer, "*OPC?\r\n")
            await close(writer)
        return reply

    assert asyncio.run(scenario()) == "1"


def test_clients_share_one_instrument() -> None:
    """A setting made by one client is seen by another, as on a real instrument."""

    async def scenario() -> str:
        """Set the frequency from one client and read it from a second."""
        server, _ = await start_generator_server()
        async with server:
            _, first_writer = await connect(server)
            first_writer.write(b"FREQ 3 GHz\n")
            await first_writer.drain()
            second_reader, second_writer = await connect(server)
            await asyncio.sleep(0.05)
            reply = await ask(second_reader, second_writer, "FREQ?\n")
            await close(first_writer)
            await close(second_writer)
        return reply

    assert asyncio.run(scenario()) == "3000000000"


def test_overlong_line_closes_only_that_connection(caplog: pytest.LogCaptureFixture) -> None:
    """A line over the size limit closes that connection; the server keeps serving."""

    async def scenario() -> tuple[bytes, str]:
        """Send an overlong line, then connect again and query."""
        server, _ = await start_generator_server()
        async with server:
            reader, writer = await connect(server)
            writer.write(b"A" * (MAX_LINE_BYTES + 10) + b"\n")
            await writer.drain()
            after_overflow = await asyncio.wait_for(reader.read(), TIMEOUT_S)
            await close(writer)
            reader, writer = await connect(server)
            reply = await ask(reader, writer, "*OPC?\n")
            await close(writer)
        return after_overflow, reply

    after_overflow, reply = asyncio.run(scenario())
    assert after_overflow == b""
    assert reply == "1"
    assert any("longer than" in record.getMessage() for record in caplog.records)


def test_abrupt_disconnect_leaves_the_server_running() -> None:
    """A client that vanishes mid-session doesn't stop the server."""

    async def scenario() -> str:
        """Connect and drop, then connect again and query."""
        server, _ = await start_generator_server()
        async with server:
            _, writer = await connect(server)
            writer.transport.abort()
            reader, writer = await connect(server)
            reply = await ask(reader, writer, "*OPC?\n")
            await close(writer)
        return reply

    assert asyncio.run(scenario()) == "1"


def test_connection_reset_by_the_client_is_survived() -> None:
    """A client that resets the connection (TCP RST) mid-session doesn't stop the server."""

    async def scenario() -> str:
        """Reset one connection while it waits for a reply, then query on a new one."""
        server, _ = await start_generator_server()
        async with server:
            client = socket.create_connection(("127.0.0.1", bound_port(server)))
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            client.sendall(b"*IDN?\n" * 1000)
            client.close()
            await asyncio.sleep(0.1)
            reader, writer = await connect(server)
            reply = await ask(reader, writer, "*OPC?\n")
            await close(writer)
        return reply

    assert asyncio.run(scenario()) == "1"


def test_compound_query_gets_one_reply_line() -> None:
    """Several queries on one line get one reply line, joined by ';', as IEEE 488.2 requires."""

    async def scenario() -> str:
        """Ask for frequency, level and output in one message."""
        server, _ = await start_generator_server()
        async with server:
            reader, writer = await connect(server)
            reply = await ask(reader, writer, "FREQ?;POW?;OUTP?\n")
            await close(writer)
        return reply

    assert asyncio.run(scenario()) == "1000000000;-30;0"


def test_handler_bug_keeps_the_connection_and_is_logged_as_such(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A handler that raises is logged as a handler error; the connection carries on."""
    calls: list[str] = []

    def flaky_handler(line: str) -> list[str]:
        """Fail on the first line, then echo."""
        calls.append(line)
        if len(calls) == 1:
            raise ValueError("bug")
        return [line]

    async def scenario() -> str:
        """Send a line that breaks the handler, then one that works."""
        server = await serve_lines(flaky_handler, "127.0.0.1", 0, name="test")
        async with server:
            reader, writer = await connect(server)
            writer.write(b"first\n")
            reply = await ask(reader, writer, "second\n")
            await close(writer)
        return reply

    assert asyncio.run(scenario()) == "second"
    messages = [record.getMessage() for record in caplog.records]
    assert "Unexpected error in a line handler" in messages
    assert not any("longer than" in message for message in messages)


def fake_server(*ports: int) -> asyncio.Server:
    """Return a stand-in server whose sockets listen on the given ports."""
    sockets = [SimpleNamespace(getsockname=lambda port=port: ("::", port)) for port in ports]
    return cast(asyncio.Server, SimpleNamespace(sockets=sockets))


def test_bound_port_is_shared_by_all_sockets() -> None:
    """A server on several addresses reports its port when every socket uses the same one."""
    scpi_port = 5025
    assert bound_port(fake_server(scpi_port, scpi_port)) == scpi_port


def test_bound_port_refuses_sockets_on_different_ports() -> None:
    """Port 0 on several addresses gives several ports; that is refused with a clear message."""
    with pytest.raises(ValueError, match="one address"):
        bound_port(fake_server(50001, 50002))


def test_a_delay_waits_before_each_line_without_holding_the_lock() -> None:
    """With a delay, each line waits first; another server on the same lock answers meanwhile."""
    delay_s = 0.5

    async def scenario() -> tuple[float, float]:
        """Ask the slow server and, while it waits, a fast server sharing its lock."""
        lock = asyncio.Lock()
        slow = await serve_lines(
            lambda line: [line],
            "127.0.0.1",
            0,
            name="scpi",
            lock=lock,
            delay_s=lambda line: delay_s if line else 0.0,
        )
        fast = await serve_lines(lambda line: [line], "127.0.0.1", 0, name="admin", lock=lock)
        async with slow, fast:
            slow_reader, slow_writer = await connect(slow)
            fast_reader, fast_writer = await connect(fast)
            started = asyncio.get_running_loop().time()
            slow_reply = asyncio.create_task(ask(slow_reader, slow_writer, "*IDN?\n"))
            await asyncio.sleep(0.05)
            assert await ask(fast_reader, fast_writer, "STATE?\n") == "STATE?"
            fast_s = asyncio.get_running_loop().time() - started
            assert await slow_reply == "*IDN?"
            slow_s = asyncio.get_running_loop().time() - started
            await close(slow_writer)
            await close(fast_writer)
        return fast_s, slow_s

    fast_s, slow_s = asyncio.run(scenario())
    assert fast_s < delay_s / 2
    assert slow_s >= delay_s
