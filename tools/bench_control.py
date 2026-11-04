"""Controlling the Docker bench from the host: admin commands, releases, logs and the suite."""

import json
import os
import socket

# This module runs docker compose and pytest, always with fixed argument lists.
import subprocess  # nosec B404
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.faults import ANALYZER, GENERATOR

ADMIN_PORTS = {GENERATOR: 5125, ANALYZER: 5126}
"""Where make bench-up publishes each instrument's admin port."""

ADMIN_TIMEOUT_S = 60.0
"""How long an admin reply may take: with slow responses on, CLEAR waits for queued lines."""

BENCH_HEALTH_TIMEOUT_S = 120
"""How long docker compose waits for both instruments to become healthy."""

BUILD_TIMEOUT_S = 900
"""How long building the image and starting the bench may take altogether."""

LOGS_TIMEOUT_S = 60
"""How long reading the instruments' logs may take."""

SUITE_TIMEOUT_S = 2700
"""How long the bench suite may take. Generous: with slow responses on, every test waits for
its own timeout, and one recorded run took over 15 minutes."""

BENCH_NOT_RUNNING_EXIT_CODE = 3
"""The bench suite's exit code when the instruments aren't answering (tests/bench/conftest.py)."""

Runner = Callable[..., subprocess.CompletedProcess[str]]
"""subprocess.run, or a stand-in in tests."""


class BenchControlError(Exception):
    """The bench couldn't be controlled; the message says what to do."""


def run_bounded(
    run: Runner,
    command: Sequence[str],
    timeout_s: float,
    what: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command with a time limit, so a stalled child process can't hang the bench tooling.

    Args:
        run: Runs the command.
        command: The command and its arguments.
        timeout_s: How long it may take.
        what: What it does, for the error message.
        env: The environment; None inherits this process's.

    Returns:
        The finished process, whatever its exit code.

    Raises:
        BenchControlError: If it didn't finish in time, so the caller can restore the bench.
    """
    try:
        return run(
            list(command), env=env, capture_output=True, text=True, check=False, timeout=timeout_s
        )
    except subprocess.TimeoutExpired as expired:
        raise BenchControlError(f"{what} didn't finish within {timeout_s:g} s") from expired


class ComposeBench:
    """The bench as make bench-up runs it: Docker Compose on this machine."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        admin_ports: Mapping[str, int] | None = None,
        run: Runner = subprocess.run,
    ) -> None:
        """Remember how to reach the bench.

        Args:
            host: Where the admin ports are published.
            admin_ports: Each instrument's admin port; None uses what make bench-up publishes.
            run: Runs a command; tests pass a recorder.
        """
        self.host = host
        self.admin_ports = ADMIN_PORTS if admin_ports is None else admin_ports
        self._run = run

    def send_admin(self, instrument: str, line: str) -> dict[str, Any]:
        """Send one admin command and return its reply.

        Args:
            instrument: GENERATOR or ANALYZER.
            line: The command, for example 'FAULT EXTRA_CABLE_LOSS_DB 1.5'.

        Returns:
            The JSON reply.

        Raises:
            BenchControlError: If the admin port can't be reached, or it refuses the command.
        """
        address = (self.host, self.admin_ports[instrument])
        try:
            with socket.create_connection(address, ADMIN_TIMEOUT_S) as connection:
                connection.sendall(f"{line}\n".encode())
                reply = connection.makefile("r", encoding="utf-8").readline()
        except OSError as error:
            raise BenchControlError(
                f"Can't reach the {instrument}'s admin port at {address[0]}:{address[1]} "
                f"({error}); start the bench with make bench-up"
            ) from error
        answer: dict[str, Any] = json.loads(reply)
        if "error" in answer:
            raise BenchControlError(f"The {instrument} refused {line!r}: {answer['error']}")
        return answer

    def start(self, firmware_version: str) -> None:
        """Build and start the bench as a firmware release, and wait until it is healthy.

        Args:
            firmware_version: The release both instruments report.

        Raises:
            BenchControlError: If docker compose fails or doesn't finish in time.
        """
        command = ["docker", "compose", "up", "--build", "--detach", "--wait"]
        command += ["--wait-timeout", str(BENCH_HEALTH_TIMEOUT_S)]
        completed = run_bounded(
            self._run,
            command,
            BUILD_TIMEOUT_S,
            f"building and starting the bench as {firmware_version}",
            env={**os.environ, "FIRMWARE_VERSION": firmware_version},
        )
        if completed.returncode != 0:
            raise BenchControlError(
                f"docker compose couldn't start the bench as {firmware_version}:\n"
                f"{completed.stderr.strip()}"
            )

    def logs_since(self, since: datetime) -> str:
        """Return both instruments' logs from a moment on.

        Args:
            since: The moment, with a time zone.

        Returns:
            The log lines, each prefixed with the container's name.

        Raises:
            BenchControlError: If docker compose can't read the logs or doesn't finish in
                time; a saved run without its logs would look like one that logged nothing.
        """
        completed = run_bounded(
            self._run,
            ["docker", "compose", "logs", "--no-color", "--since", since.isoformat()],
            LOGS_TIMEOUT_S,
            "reading the instruments' logs",
        )
        if completed.returncode != 0:
            raise BenchControlError(
                f"docker compose couldn't read the logs: {completed.stderr.strip()}"
            )
        return completed.stdout


class PytestSuite:
    """Runs the bench suite in its own process, as make bench-test does."""

    def __init__(self, run: Runner = subprocess.run) -> None:
        """Remember how to run commands.

        Args:
            run: Runs a command; tests pass a recorder.
        """
        self._run = run

    def run(self, results_path: Path, firmware_version: str) -> None:
        """Run every bench test, writing the JSON results file; failing tests are expected.

        Args:
            results_path: Where the results file goes.
            firmware_version: The release the bench runs, which the identity test expects.

        Raises:
            BenchControlError: If the suite didn't finish in time, the bench wasn't running,
                or no results file was written.
        """
        command: Sequence[str] = [
            sys.executable,
            "-m",
            "pytest",
            "tests/bench",
            "--no-cov",
            "-p",
            "no:cacheprovider",
            "-q",
        ]
        environment = {
            **os.environ,
            "BENCH_RESULTS_JSON": str(results_path),
            "FIRMWARE_VERSION": firmware_version,
        }
        completed = run_bounded(
            self._run, command, SUITE_TIMEOUT_S, "the bench suite", env=environment
        )
        if completed.returncode == BENCH_NOT_RUNNING_EXIT_CODE:
            raise BenchControlError("The bench isn't running; start it with make bench-up")
        if not results_path.exists():
            raise BenchControlError(
                f"The bench suite wrote no results file:\n{completed.stdout[-2000:]}"
            )
