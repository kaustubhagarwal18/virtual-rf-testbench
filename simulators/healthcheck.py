"""Container healthcheck: is a VirtualRF instrument answering SCPI on this port?

Docker runs it as: python -m simulators.healthcheck --port 5025
It exits 0 when *IDN? gets a VirtualRF reply, and 1 otherwise, printing one line either way;
docker inspect shows that line in the container's health log.
"""

import argparse
import socket
import sys
from collections.abc import Sequence
from dataclasses import dataclass

MANUFACTURER_PREFIX = "VirtualRF,"


@dataclass(frozen=True)
class Health:
    """The outcome of one check.

    Attributes:
        healthy: Whether a VirtualRF instrument answered *IDN?.
        detail: The instrument's identity when healthy, otherwise why the check failed.
    """

    healthy: bool
    detail: str


def check_instrument(host: str, port: int, timeout_s: float) -> Health:
    """Ask an instrument to identify itself.

    Args:
        host: The instrument's address.
        port: Its SCPI port.
        timeout_s: How long connecting and the reply may each take.

    Returns:
        Healthy with the identity, or unhealthy with the reason.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as connection:
            connection.sendall(b"*IDN?\n")
            reply = connection.makefile("r", encoding="ascii", errors="replace").readline(256)
    except TimeoutError:
        return Health(False, f"no reply to *IDN? from {host}:{port} within {timeout_s} s")
    except OSError as error:
        return Health(False, f"can't connect to {host}:{port}: {error}")
    reply = reply.strip()
    if not reply:
        return Health(False, f"no reply to *IDN? from {host}:{port}")
    if not reply.startswith(MANUFACTURER_PREFIX):
        return Health(False, f"{host}:{port} is not a VirtualRF instrument: {reply!r}")
    return Health(True, reply)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the check from the command line.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.

    Returns:
        0 if healthy, 1 if not.
    """
    parser = argparse.ArgumentParser(
        prog="python -m simulators.healthcheck",
        description="Check that a VirtualRF instrument answers *IDN?.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="instrument address")
    parser.add_argument("--port", type=int, default=5025, help="SCPI port")
    parser.add_argument("--timeout-s", type=float, default=2.0, help="seconds to wait")
    parsed = parser.parse_args(arguments)
    health = check_instrument(parsed.host, parsed.port, parsed.timeout_s)
    sys.stdout.write(f"{'healthy' if health.healthy else 'unhealthy'}: {health.detail}\n")
    return 0 if health.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
