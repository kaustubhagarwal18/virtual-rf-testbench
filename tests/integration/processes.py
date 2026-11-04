"""Running a simulator as its own process, the way Docker will run it."""

import contextlib
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Sequence

STARTUP_TIMEOUT_S = 10.0


@contextlib.contextmanager
def simulator_process(
    module: str,
    arguments: Sequence[str],
    *,
    firmware_version: str,
    ready_message: str,
    startup_timeout_s: float = STARTUP_TIMEOUT_S,
) -> Iterator[dict[str, object]]:
    """Start a simulator module and wait for its log line saying it is listening.

    A background thread reads the process's stderr for as long as it runs, so a busy
    simulator can never block on a full pipe.

    Args:
        module: The module to run with python -m, for example 'simulators.signal_generator'.
        arguments: Its command-line arguments.
        firmware_version: The SIMULATOR_FIRMWARE_VERSION to start it with.
        ready_message: The log message that means it is listening.
        startup_timeout_s: How long to wait for that message.

    Yields:
        That log entry, which holds the ports it chose.
    """
    environment = {**os.environ, "SIMULATOR_FIRMWARE_VERSION": firmware_version}
    command = [sys.executable, "-m", module, *arguments]
    with subprocess.Popen(command, stderr=subprocess.PIPE, text=True, env=environment) as process:
        lines: queue.Queue[str | None] = queue.Queue()

        def drain() -> None:
            """Copy every stderr line into the queue, then mark the end."""
            assert process.stderr is not None
            for line in process.stderr:
                lines.put(line)
            lines.put(None)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            yield _wait_until_ready(process, lines, module, ready_message, startup_timeout_s)
        finally:
            process.terminate()
            process.wait(STARTUP_TIMEOUT_S)
            reader.join(STARTUP_TIMEOUT_S)


def _wait_until_ready(
    process: subprocess.Popen[str],
    lines: "queue.Queue[str | None]",
    module: str,
    ready_message: str,
    startup_timeout_s: float,
) -> dict[str, object]:
    """Read log lines until the ready message arrives, the process exits, or time runs out.

    Args:
        process: The simulator process.
        lines: Its stderr lines; None marks the end of the stream.
        module: The module name, for error messages.
        ready_message: The log message that means it is listening.
        startup_timeout_s: How long to wait.

    Returns:
        The log entry with the ready message.

    Raises:
        RuntimeError: If the process exits first, with its exit code and stderr, or if the
            message doesn't arrive in time.
    """
    deadline = time.monotonic() + startup_timeout_s
    seen: list[str] = []
    while (remaining_s := deadline - time.monotonic()) > 0:
        try:
            line = lines.get(timeout=remaining_s)
        except queue.Empty:
            break
        if line is None:
            exit_code = process.wait(STARTUP_TIMEOUT_S)
            raise RuntimeError(
                f"{module} exited with code {exit_code} before it was ready:\n{''.join(seen)}"
            )
        seen.append(line)
        with contextlib.suppress(ValueError):
            entry: dict[str, object] = json.loads(line)
            if entry.get("message") == ready_message:
                return entry
    raise RuntimeError(f"{module} didn't log {ready_message!r} within {startup_timeout_s} s")


def ask(port: object, line: str) -> str:
    """Send one line to a local port and return the reply line.

    Args:
        port: The port, as read from a log entry.
        line: The line to send, without its line ending.

    Returns:
        The reply, without its line ending.
    """
    assert isinstance(port, int)
    with socket.create_connection(("127.0.0.1", port), timeout=STARTUP_TIMEOUT_S) as connection:
        connection.sendall(f"{line}\n".encode())
        return connection.makefile("r").readline().strip()
