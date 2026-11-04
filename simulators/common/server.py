"""An asyncio TCP server that passes text lines to a handler and sends back its replies.

Both the SCPI port and the admin port use it. Every connection shares the one handler, as
every controller on a real instrument shares its one set of settings.

Handlers run in a worker thread, so a handler that blocks (the analyzer asking the generator
for its state) never stalls the event loop. Each instrument runs one handler at a time: the
others wait on an asyncio lock on the loop, as a busy instrument queues commands, without
holding a worker thread while they wait. A generator sharing the analyzer's loop and thread
pool therefore always has a thread free to answer.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

MAX_LINE_BYTES = 64 * 1024
"""The longest line accepted; a longer one closes that connection."""

LineHandler = Callable[[str], list[str]]
"""Takes one received line and returns the replies to send, possibly none."""

_LOGGER = logging.getLogger(__name__)


async def serve_lines(
    handler: LineHandler,
    host: str,
    port: int,
    *,
    name: str,
    lock: asyncio.Lock | None = None,
    connection_log_level: int = logging.INFO,
    delay_s: Callable[[], float] | None = None,
) -> asyncio.Server:
    """Start a server that runs each received line through a handler.

    All replies to one line go back as one line joined by ';', as IEEE 488.2 requires for a
    message with several queries, so a client reading one line per query stays in step.

    Args:
        handler: Turns a received line into replies.
        host: The address to listen on.
        port: The port to listen on; 0 picks a free port.
        name: What this server is, for the logs, for example 'scpi' or 'admin'.
        lock: Held while the handler runs; servers on one instrument share it. None gives
            this server its own.
        connection_log_level: The level for 'client connected' and 'disconnected' logs.
        delay_s: Asked before each line; the line then waits that many seconds before it
            takes the lock, so a slow instrument never blocks servers sharing the lock.
            None means no delay.

    Returns:
        The started server.
    """
    instrument_lock = asyncio.Lock() if lock is None else lock

    async def run_one_at_a_time(line: str, peer: str) -> list[str]:
        """Wait for the instrument, then run the handler on one line in a worker thread.

        Args:
            line: The received line.
            peer: The client's address, for the logs.

        Returns:
            The handler's replies.
        """
        if delay_s is not None and (seconds := delay_s()) > 0:
            await asyncio.sleep(seconds)
        async with instrument_lock:
            return await asyncio.to_thread(_run_handler, handler, line, name, peer)

    async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one client connection until it closes.

        Args:
            reader: The connection's incoming stream.
            writer: The connection's outgoing stream.
        """
        await _serve_connection(run_one_at_a_time, name, connection_log_level, reader, writer)

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
    run_line: Callable[[str, str], Awaitable[list[str]]],
    name: str,
    connection_log_level: int,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Read lines, run each through the handler and send its replies, until the client leaves.

    Args:
        run_line: Turns a received line and the client's address into replies.
        name: What this server is, for the logs.
        connection_log_level: The level for 'client connected' and 'disconnected' logs.
        reader: The connection's incoming stream.
        writer: The connection's outgoing stream.
    """
    peer = str(writer.get_extra_info("peername"))
    _LOGGER.log(connection_log_level, "Client connected", extra={"server": name, "peer": peer})
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
            replies = await run_line(line, peer)
            if replies:
                writer.write((";".join(replies) + "\n").encode("ascii", errors="replace"))
                await writer.drain()
    except ConnectionError:
        _LOGGER.info("Connection dropped by the client", extra={"server": name, "peer": peer})
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
        _LOGGER.log(
            connection_log_level, "Client disconnected", extra={"server": name, "peer": peer}
        )
