"""Running a simulator as its own process, the way Docker will run it."""

import contextlib
import json
import os
import socket
import subprocess
import sys
from collections.abc import Iterator, Sequence

STARTUP_TIMEOUT_S = 10.0


@contextlib.contextmanager
def simulator_process(
    module: str, arguments: Sequence[str], firmware_version: str, ready_message: str
) -> Iterator[dict[str, object]]:
    """Start a simulator module and wait for its log line saying it is listening.

    Args:
        module: The module to run with python -m, for example 'simulators.signal_generator'.
        arguments: Its command-line arguments.
        firmware_version: The SIMULATOR_FIRMWARE_VERSION to start it with.
        ready_message: The log message that means it is listening.

    Yields:
        That log entry, which holds the ports it chose.
    """
    environment = {**os.environ, "SIMULATOR_FIRMWARE_VERSION": firmware_version}
    command = [sys.executable, "-m", module, *arguments]
    with subprocess.Popen(command, stderr=subprocess.PIPE, text=True, env=environment) as process:
        try:
            assert process.stderr is not None
            entry: dict[str, object] = {}
            for line in process.stderr:
                entry = json.loads(line)
                if entry["message"] == ready_message:
                    break
            yield entry
        finally:
            process.terminate()
            process.wait(STARTUP_TIMEOUT_S)


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
