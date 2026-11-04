"""A scriptable fake instrument on a socket, for failures the simulators never produce."""

import socketserver
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from tests.utilities.tcp import serving_in_background


@dataclass
class Script:
    """How the fake instrument behaves.

    Attributes:
        idn: What it answers to *IDN?.
        silent: Queries it never answers, without queuing an error.
        drop: A command that makes it close the connection.
    """

    idn: str = "VirtualRF,FAKE-1,000001,0.0.1"
    silent: set[str] = field(default_factory=set)
    drop: str = "DROP"


class _Handler(socketserver.StreamRequestHandler):
    """Answers the commands RsInstrument sends, as the script says."""

    def handle(self) -> None:
        """Serve one connection line by line."""
        assert isinstance(self.server, _FakeInstrumentServer)
        script = self.server.script
        replies = {"*IDN?": script.idn, "*OPC?": "1", "*STB?": "0", "SYST:ERR?": '0,"No error"'}
        for raw in self.rfile:
            command = raw.decode().strip()
            if command == script.drop:
                return
            if command in replies and command not in script.silent:
                self.wfile.write(f"{replies[command]}\n".encode())


class _FakeInstrumentServer(socketserver.ThreadingTCPServer):
    """A local TCP server that runs one script."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, script: Script) -> None:
        """Listen on a free local port.

        Args:
            script: How to behave.
        """
        super().__init__(("127.0.0.1", 0), _Handler)
        self.script = script


@contextmanager
def fake_instrument(script: Script) -> Iterator[int]:
    """Run a fake instrument in a background thread.

    Args:
        script: How it behaves.

    Yields:
        The port it listens on.
    """
    with serving_in_background(_FakeInstrumentServer(script)) as port:
        yield port
