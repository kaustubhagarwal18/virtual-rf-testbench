"""Tests for controlling the Docker bench: admin commands, releases, logs and the suite."""

import socket
import socketserver
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.utilities.tcp import serving_in_background
from tools.bench_control import (
    BENCH_NOT_RUNNING_EXIT_CODE,
    BenchControlError,
    ComposeBench,
    PytestSuite,
)
from tools.faults import ANALYZER


class _OneLineHandler(socketserver.StreamRequestHandler):
    """Records the line received and answers with the server's reply."""

    def handle(self) -> None:
        """Answer one line."""
        assert isinstance(self.server, FakeAdminPort)
        self.server.received.append(self.rfile.readline().decode())
        self.wfile.write(self.server.reply.encode())


class FakeAdminPort(socketserver.ThreadingTCPServer):
    """A local admin port that answers every command with one fixed line."""

    daemon_threads = True

    def __init__(self, reply: str) -> None:
        """Listen on a free local port.

        Args:
            reply: The line to answer with.
        """
        super().__init__(("127.0.0.1", 0), _OneLineHandler)
        self.reply = reply
        self.received: list[str] = []


class RecordingRunner:
    """Stands in for subprocess.run: records each call and returns a chosen result."""

    def __init__(
        self, returncode: int = 0, stdout: str = "", stderr: str = "", *, times_out: bool = False
    ) -> None:
        """Choose the result every call gets, or make every call time out."""
        self.result = (returncode, stdout, stderr)
        self.times_out = times_out
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.before_return: list[Path] = []

    def __call__(self, command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        """Record the call, create any files a real run would, and return the result.

        Raises:
            TimeoutExpired: If this runner was built to time out.
        """
        self.calls.append((command, options))
        if self.times_out:
            raise subprocess.TimeoutExpired(command, options.get("timeout") or 0)
        for path in self.before_return:
            path.write_text("{}", encoding="utf-8")
        returncode, stdout, stderr = self.result
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_an_admin_command_goes_to_the_instruments_port_and_returns_its_reply() -> None:
    """The command is sent as one line to that instrument's port, and its JSON reply comes back."""
    server = FakeAdminPort('{"cleared": []}\n')
    with serving_in_background(server) as port:
        bench = ComposeBench(admin_ports={ANALYZER: port})
        assert bench.send_admin(ANALYZER, "CLEAR") == {"cleared": []}
    assert server.received == ["CLEAR\n"]


def test_a_refused_admin_command_raises_with_the_instruments_reason() -> None:
    """An error reply becomes BenchControlError, naming the instrument and the command."""
    with serving_in_background(FakeAdminPort('{"error": "Unknown fault X"}\n')) as port:
        bench = ComposeBench(admin_ports={ANALYZER: port})
        with pytest.raises(
            BenchControlError, match="analyzer refused 'FAULT X 1': Unknown fault X"
        ):
            bench.send_admin(ANALYZER, "FAULT X 1")


def test_an_unreachable_admin_port_says_to_start_the_bench() -> None:
    """A closed port becomes a one-line hint to run make bench-up."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    bench = ComposeBench(admin_ports={ANALYZER: closed_port})
    with pytest.raises(BenchControlError, match="start the bench with make bench-up"):
        bench.send_admin(ANALYZER, "CLEAR")


def test_starting_a_release_builds_with_its_version_and_waits() -> None:
    """Docker compose up builds, waits for health, and gets the release as FIRMWARE_VERSION."""
    runner = RecordingRunner()
    ComposeBench(run=runner).start("0.2.0-rc1")
    command, options = runner.calls[0]
    assert command[:6] == ["docker", "compose", "up", "--build", "--detach", "--wait"]
    assert options["env"]["FIRMWARE_VERSION"] == "0.2.0-rc1"


def test_a_failed_start_raises_with_compose_output() -> None:
    """If docker compose fails, its error output is in the message."""
    runner = RecordingRunner(returncode=1, stderr="daemon not running")
    with pytest.raises(BenchControlError, match="daemon not running"):
        ComposeBench(run=runner).start("0.1.0")


def test_logs_are_read_from_the_runs_start() -> None:
    """Docker compose logs is asked for lines since the given moment."""
    runner = RecordingRunner(stdout="generator-1  | {}\n")
    since = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    assert ComposeBench(run=runner).logs_since(since) == "generator-1  | {}\n"
    assert runner.calls[0][0][-2:] == ["--since", "2026-11-04T12:00:00+00:00"]


def test_logs_that_cant_be_read_raise_instead_of_coming_back_empty() -> None:
    """A failed docker compose logs call is an error, never an empty log."""
    runner = RecordingRunner(returncode=1, stderr="no such service")
    with pytest.raises(BenchControlError, match="couldn't read the logs: no such service"):
        ComposeBench(run=runner).logs_since(datetime(2026, 11, 4, tzinfo=UTC))


def test_the_suite_runs_the_bench_tests_with_the_results_path_and_release(tmp_path: Path) -> None:
    """Pytest runs tests/bench, told where to write results and which release to expect."""
    results = tmp_path / "results.json"
    runner = RecordingRunner(returncode=1)
    runner.before_return.append(results)
    PytestSuite(run=runner).run(results, "0.2.0-rc1")
    command, options = runner.calls[0]
    assert command[1:4] == ["-m", "pytest", "tests/bench"]
    assert options["env"]["BENCH_RESULTS_JSON"] == str(results)
    assert options["env"]["FIRMWARE_VERSION"] == "0.2.0-rc1"


def test_a_suite_that_found_no_bench_says_to_start_it(tmp_path: Path) -> None:
    """The suite's 'bench not running' exit code becomes a hint to run make bench-up."""
    runner = RecordingRunner(returncode=BENCH_NOT_RUNNING_EXIT_CODE)
    with pytest.raises(BenchControlError, match="make bench-up"):
        PytestSuite(run=runner).run(tmp_path / "results.json", "0.1.0")


def test_a_suite_that_wrote_no_results_says_so(tmp_path: Path) -> None:
    """Without a results file, the error shows the end of pytest's output."""
    runner = RecordingRunner(returncode=4, stdout="ERROR: usage")
    with pytest.raises(BenchControlError, match="no results file:\nERROR: usage"):
        PytestSuite(run=runner).run(tmp_path / "results.json", "0.1.0")


def test_every_child_process_gets_a_timeout(tmp_path: Path) -> None:
    """Building, reading logs and running the suite are all bounded, so none can stall forever."""
    results = tmp_path / "results.json"
    runner = RecordingRunner()
    runner.before_return.append(results)
    bench = ComposeBench(run=runner)
    bench.start("0.1.0")
    bench.logs_since(datetime(2026, 11, 4, tzinfo=UTC))
    PytestSuite(run=runner).run(results, "0.1.0")
    timeouts = [options["timeout"] for _command, options in runner.calls]
    assert len(timeouts) == len(runner.calls)
    assert all(timeout > 0 for timeout in timeouts)


@pytest.mark.parametrize("action", ["start", "logs", "suite"])
def test_a_stalled_child_process_becomes_a_bench_error(action: str, tmp_path: Path) -> None:
    """A timed-out child process is reported, so run_fault still reaches its restore step."""
    runner = RecordingRunner(times_out=True)
    bench = ComposeBench(run=runner)
    actions: dict[str, Callable[[], object]] = {
        "start": lambda: bench.start("0.1.0"),
        "logs": lambda: bench.logs_since(datetime(2026, 11, 4, tzinfo=UTC)),
        "suite": lambda: PytestSuite(run=runner).run(tmp_path / "results.json", "0.1.0"),
    }
    with pytest.raises(BenchControlError, match="didn't finish within"):
        actions[action]()
