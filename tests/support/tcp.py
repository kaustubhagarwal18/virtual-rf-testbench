"""Running a socketserver in a background thread for the length of a test."""

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
