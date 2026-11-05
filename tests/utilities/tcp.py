"""TCP helpers for tests: a socketserver in a background thread, and asking a port one line."""

import asyncio
import socket
import socketserver
import threading
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def serving_in_background(server: socketserver.TCPServer) -> Iterator[int]:
    """Serve in a background thread, then shut the server down.

    Args:
        server: A server already bound to a local port.

    Yields:
        The port it listens on.
    """
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


async def ask_line(port: int, line: str, timeout_s: float = 5.0) -> str:
    """Send one line to a local port and return the reply line.

    Args:
        port: The port.
        line: The line to send, without its line ending.
        timeout_s: How long to wait for the reply.

    Returns:
        The reply, without its line ending.
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"{line}\n".encode())
    await writer.drain()
    reply = await asyncio.wait_for(reader.readline(), timeout_s)
    writer.close()
    await writer.wait_closed()
    return reply.decode().strip()


def closed_port() -> int:
    """Return a local port nothing listens on, for testing what happens when a connect fails.

    Returns:
        The port, free at the moment it is returned.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port
