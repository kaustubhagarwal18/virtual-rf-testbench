"""Tests for the analyzer's link to the generator's admin port."""

import socket
import socketserver
from collections.abc import Iterator

import pytest

from simulators.common.physics import SourceState
from simulators.spectrum_analyzer.link import NO_SIGNAL, AdminPortLink, parse_state
from tests.utilities.tcp import serving_in_background


class _Responder(socketserver.StreamRequestHandler):
    """Answers each line with the reply stored on the server."""

    def handle(self) -> None:
        """Send the canned reply for every line received."""
        assert isinstance(self.server, _FakeAdminServer)
        for _ in self.rfile:
            self.wfile.write(self.server.reply)


class _FakeAdminServer(socketserver.ThreadingTCPServer):
    """A local TCP server that answers every line with one canned reply."""

    daemon_threads = True

    def __init__(self, reply: bytes) -> None:
        """Listen on a free local port.

        Args:
            reply: What to send back for each line.
        """
        super().__init__(("127.0.0.1", 0), _Responder)
        self.reply = reply


@pytest.fixture(name="admin_port")
def fixture_admin_port(request: pytest.FixtureRequest) -> Iterator[int]:
    """Run a fake admin port that answers with the reply given by the test's parameter."""
    with serving_in_background(_FakeAdminServer(request.param)) as port:
        yield port


@pytest.mark.parametrize(
    "admin_port",
    [b'{"frequency_hz": 2000000000.0, "level_dbm": -12.0, "output_on": true}\n'],
    indirect=True,
)
def test_state_is_read_from_the_admin_port(admin_port: int) -> None:
    """The link turns the generator's STATE? reply into a SourceState."""
    assert AdminPortLink("127.0.0.1", admin_port).source_state() == SourceState(
        frequency_hz=2e9, level_dbm=-12.0, output_on=True
    )


@pytest.mark.parametrize(
    "admin_port",
    [
        b"not json\n",
        b"[1, 2]\n",
        b'{"error": "Unknown admin command: STATE?"}\n',
        b'{"frequency_hz": "high", "level_dbm": -12.0, "output_on": true}\n',
        b'{"frequency_hz": 1e9, "level_dbm": -12.0, "output_on": "yes"}\n',
        b"",
        b'{"frequency_hz": NaN, "level_dbm": -12.0, "output_on": true}\n',
        b'{"frequency_hz": 1e9, "level_dbm": Infinity, "output_on": true}\n',
        b'{"frequency_hz": 0, "level_dbm": -12.0, "output_on": true}\n',
        b'{"frequency_hz": -5e6, "level_dbm": -12.0, "output_on": true}\n',
        b'{"frequency_hz": 1' + b"0" * 400 + b', "level_dbm": -12.0, "output_on": true}\n',
        b"[" * 100_000 + b"\n",
    ],
    indirect=True,
)
def test_bad_replies_mean_no_signal(admin_port: int, caplog: pytest.LogCaptureFixture) -> None:
    """A reply that isn't a valid state is treated as no signal, and logged."""
    assert AdminPortLink("127.0.0.1", admin_port, timeout_s=0.1).source_state() == NO_SIGNAL
    assert any("Generator link unavailable" in record.getMessage() for record in caplog.records)


def test_unreachable_generator_means_no_signal(caplog: pytest.LogCaptureFixture) -> None:
    """With nothing listening, the analyzer sees no signal, like an unplugged cable."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    assert AdminPortLink("127.0.0.1", closed_port, timeout_s=0.1).source_state() == NO_SIGNAL
    assert any("Generator link unavailable" in record.getMessage() for record in caplog.records)


def test_deeply_nested_json_is_rejected_as_a_bad_state() -> None:
    """JSON nested too deeply to parse is a bad state, not a crash."""
    with pytest.raises(ValueError, match="nested too deeply"):
        parse_state("[" * 100_000)
