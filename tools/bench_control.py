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

BENCH_START_TIMEOUT_S = 120
"""How long docker compose may take to build and start a healthy bench."""

BENCH_NOT_RUNNING_EXIT_CODE = 3
"""The bench suite's exit code when the instruments aren't answering (tests/bench/conftest.py)."""

Runner = Callable[..., subprocess.CompletedProcess[str]]
"""subprocess.run, or a stand-in in tests."""


class BenchControlError(Exception):
    """The bench couldn't be controlled; the message says what to do."""


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
            BenchControlError: If docker compose fails.
        """
        command = ["docker", "compose", "up", "--build", "--detach", "--wait"]
        command += ["--wait-timeout", str(BENCH_START_TIMEOUT_S)]
        completed = self._run(
            command,
            env={**os.environ, "FIRMWARE_VERSION": firmware_version},
            capture_output=True,
            text=True,
            check=False,
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
            BenchControlError: If docker compose can't read the logs; a saved run without
                its logs would look like a run that logged nothing.
        """
        completed = self._run(
            ["docker", "compose", "logs", "--no-color", "--since", since.isoformat()],
            capture_output=True,
            text=True,
            check=False,
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
            BenchControlError: If the bench wasn't running, or no results file was written.
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
        completed = self._run(command, env=environment, capture_output=True, text=True, check=False)
        if completed.returncode == BENCH_NOT_RUNNING_EXIT_CODE:
            raise BenchControlError("The bench isn't running; start it with make bench-up")
        if not results_path.exists():
            raise BenchControlError(
                f"The bench suite wrote no results file:\n{completed.stdout[-2000:]}"
            )
