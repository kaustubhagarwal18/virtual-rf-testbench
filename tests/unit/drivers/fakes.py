"""A fake SCPI session for driver unit tests: canned replies, recorded commands (ADR 0001)."""

from collections.abc import Mapping

from drivers.session import InstrumentError, InstrumentTimeoutError, ScpiSession


class FakeSession:
    """Answers queries from a table, records every command, and fails where a test says so."""

    def __init__(
        self,
        replies: Mapping[str, str] | None = None,
        failures: Mapping[str, InstrumentError] | None = None,
    ) -> None:
        """Set up the canned replies and failures.

        Args:
            replies: Query replies by exact command; *OPC? answers '1' unless overridden.
            failures: Errors to raise when a given command is sent, as an instrument would.
        """
        self.replies = {"*OPC?": "1", **(replies or {})}
        self.failures = dict(failures or {})
        self.sent: list[str] = []

    def write(self, command: str) -> None:
        """Record a command, failing if the test asked for that.

        Args:
            command: The command sent.
        """
        self._send(command)

    def query(self, command: str) -> str:
        """Record a query and return its canned reply.

        Args:
            command: The query sent.

        Returns:
            The reply from the table.

        Raises:
            InstrumentTimeoutError: If the table has no reply, as a real instrument stays silent.
        """
        self._send(command)
        if command not in self.replies:
            raise InstrumentTimeoutError(f"No reply to {command!r}")
        return self.replies[command]

    def _send(self, command: str) -> None:
        """Record a command and raise its configured failure, if any.

        Args:
            command: The command sent.

        Raises:
            InstrumentError: The failure configured for this command.
        """
        self.sent.append(command)
        if command in self.failures:
            raise self.failures[command]


def check_fake_matches_the_interface() -> ScpiSession:
    """Let mypy check that the fake really implements ScpiSession.

    Returns:
        A fake session, typed as the interface.
    """
    return FakeSession()
