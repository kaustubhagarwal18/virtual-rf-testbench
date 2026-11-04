"""An asyncio TCP server that passes text lines to a handler and sends back its replies.

Both the SCPI port and the admin port use it. Every connection shares the one handler, as
every controller on a real instrument shares its one set of settings.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable

MAX_LINE_BYTES = 64 * 1024
"""The longest line accepted; a longer one closes that connection."""

LineHandler = Callable[[str], list[str]]
"""Takes one received line and returns the replies to send, possibly none."""

_LOGGER = logging.getLogger(__name__)


async def serve_lines(handler: LineHandler, host: str, port: int, *, name: str) -> asyncio.Server:
    """Start a server that runs each received line through a handler.

    All replies to one line go back as one line joined by ';', as IEEE 488.2 requires for a
    message with several queries, so a client reading one line per query stays in step.

    Args:
        handler: Turns a received line into replies.
        host: The address to listen on.
        port: The port to listen on; 0 picks a free port.
        name: What this server is, for the logs, for example 'scpi' or 'admin'.

    Returns:
        The started server.
    """

    async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one client connection until it closes.

        Args:
            reader: The connection's incoming stream.
            writer: The connection's outgoing stream.
        """
        await _serve_connection(handler, name, reader, writer)

    return await asyncio.start_server(handle_connection, host, port, limit=MAX_LINE_BYTES)


def bound_port(server: asyncio.Server) -> int:
    """Return the port a server listens on, which matters when it was started on port 0.

    Args:
        server: A started server.

    Returns:
        The port all of its sockets share.

    Raises:
        ValueError: If its sockets listen on different ports, which happens with port 0 on a
            host name that resolves to several addresses.
    """
    ports = {socket.getsockname()[1] for socket in server.sockets}
    if len(ports) != 1:
        raise ValueError(
            f"The server listens on several ports {sorted(ports)}; use one address with port 0"
        )
    port: int = ports.pop()
    return port


def _run_handler(handler: LineHandler, line: str, name: str, peer: str) -> list[str]:
    """Run the handler on one line, so that a bug in it can't drop the connection.

    Args:
        handler: Turns a received line into replies.
        line: The received line.
        name: What this server is, for the logs.
        peer: The client's address, for the logs.

    Returns:
        The handler's replies, or none if it raised; the error is logged with its traceback.
    """
    try:
        return handler(line)
    # A handler bug must not drop the connection; it is logged with its traceback instead.
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception(
            "Unexpected error in a line handler",
            extra={"server": name, "peer": peer, "line": line[:200]},
        )
        return []


async def _serve_connection(
    handler: LineHandler, name: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """Read lines, run each through the handler and send its replies, until the client leaves.

    Args:
        handler: Turns a received line into replies.
        name: What this server is, for the logs.
        reader: The connection's incoming stream.
        writer: The connection's outgoing stream.
    """
    peer = str(writer.get_extra_info("peername"))
    _LOGGER.info("Client connected", extra={"server": name, "peer": peer})
    try:
        while True:
            try:
                received = await reader.readline()
            except ValueError:
                _LOGGER.warning(
                    "Line longer than %d bytes; closing the connection",
                    MAX_LINE_BYTES,
                    extra={"server": name, "peer": peer},
                )
                break
            if not received:
                break
            line = received.decode("ascii", errors="replace").rstrip("\r\n")
            replies = _run_handler(handler, line, name, peer)
            if replies:
                writer.write((";".join(replies) + "\n").encode("ascii", errors="replace"))
                await writer.drain()
    except ConnectionError:
        _LOGGER.info("Connection dropped by the client", extra={"server": name, "peer": peer})
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
        _LOGGER.info("Client disconnected", extra={"server": name, "peer": peer})
