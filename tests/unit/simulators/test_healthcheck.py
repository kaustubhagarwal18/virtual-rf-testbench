"""Tests for the container healthcheck: is a VirtualRF instrument answering SCPI on a port?"""

import socket
import socketserver
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from simulators.healthcheck import check_instrument, main
from tests.support.tcp import serving_in_background


class _OneReplyHandler(socketserver.StreamRequestHandler):
    """Answers *IDN? with the reply stored on its server, or holds the line silently if None."""

    def handle(self) -> None:
        """Answer one line."""
        assert isinstance(self.server, _FakeInstrument)
        self.rfile.readline()
        if self.server.reply is None:
            self.rfile.read()
        else:
            self.wfile.write(self.server.reply)


class _FakeInstrument(socketserver.ThreadingTCPServer):
    """A local server that gives one fixed answer to *IDN?."""

    daemon_threads = True

    def __init__(self, reply: bytes | None) -> None:
        """Listen on a free local port.

        Args:
            reply: What to answer, or None to stay silent.
        """
        super().__init__(("127.0.0.1", 0), _OneReplyHandler)
        self.reply = reply


@contextmanager
def instrument_answering(reply: bytes | None) -> Iterator[int]:
    """Run a fake instrument and yield its port."""
    with serving_in_background(_FakeInstrument(reply)) as port:
        yield port


def closed_port() -> int:
    """Return a local port nothing listens on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def test_virtualrf_instrument_is_healthy() -> None:
    """A VirtualRF *IDN? reply means the instrument is up and answering SCPI."""
    with instrument_answering(b"VirtualRF,SG-1,100001,1.0.0\n") as port:
        health = check_instrument("127.0.0.1", port, timeout_s=1.0)
    assert health.healthy
    assert health.detail == "VirtualRF,SG-1,100001,1.0.0"


@pytest.mark.parametrize(
    ("reply", "problem"),
    [
        (b"Acme,X-1,1,1.0\n", "not a VirtualRF instrument"),
        (b"", "no reply"),
        (None, "no reply"),
    ],
)
def test_wrong_or_missing_identity_is_unhealthy(reply: bytes | None, problem: str) -> None:
    """Another instrument, an empty reply or silence all count as unhealthy, with the reason."""
    with instrument_answering(reply) as port:
        health = check_instrument("127.0.0.1", port, timeout_s=0.3)
    assert not health.healthy
    assert problem in health.detail


def test_closed_port_is_unhealthy() -> None:
    """Nothing listening means unhealthy, saying the connection failed."""
    health = check_instrument("127.0.0.1", closed_port(), timeout_s=0.3)
    assert not health.healthy
    assert "can't connect" in health.detail


def test_main_exits_zero_when_healthy(capsys: pytest.CaptureFixture[str]) -> None:
    """The command exits 0 and prints the identity, which docker inspect then shows."""
    with instrument_answering(b"VirtualRF,SA-1,200001,1.0.0\n") as port:
        assert main(["--port", str(port)]) == 0
    assert "VirtualRF,SA-1,200001,1.0.0" in capsys.readouterr().out


def test_main_exits_one_with_a_reason_when_unhealthy(capsys: pytest.CaptureFixture[str]) -> None:
    """The command exits 1 and prints one line saying why."""
    assert main(["--port", str(closed_port()), "--timeout-s", "0.3"]) == 1
    assert "can't connect" in capsys.readouterr().out


def test_runs_as_a_module() -> None:
    """Python -m simulators.healthcheck works the way Docker calls it."""
    completed = subprocess.run(
        [sys.executable, "-m", "simulators.healthcheck", "--port", str(closed_port())],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
