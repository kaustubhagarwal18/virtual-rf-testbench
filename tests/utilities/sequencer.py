"""A test sequence log: one line per step, so a generic failure can be traced to its parameter.

Instrument test sequencers report a failed step by number and leave the detail in their own
log file. That is deliberately reproduced here: when a step fails, the test fails with the
step's name and nothing else, and the parameter, the value and the reason go to
`sequence.log`, which is saved with the run.

Nothing is lost: every reason is written in full, and the saved log is part of a run's
evidence. The point is that the reason isn't in the test's message, which is what makes
these failures expensive for a person and worth a triage assistant.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SEQUENCE_LOG_VARIABLE = "BENCH_SEQUENCE_LOG"
"""Where the sequence log is written."""

DEFAULT_SEQUENCE_LOG = Path("reports/sequence.log")
"""Where it goes when nothing says otherwise."""


class StepFailedError(Exception):
    """A sequence step failed. The message names the step; the reason is in the log."""


def sequence_log_path(environ: dict[str, str] | None = None) -> Path:
    """Return where the sequence log is written.

    Args:
        environ: Where to look; None reads the process environment.

    Returns:
        The log's path.
    """
    source = os.environ if environ is None else environ
    return Path(source.get(SEQUENCE_LOG_VARIABLE, str(DEFAULT_SEQUENCE_LOG)))


class Sequencer:
    """Runs named steps against the instruments, logging each one to the sequence log."""

    def __init__(self, path: Path | None = None) -> None:
        """Open the sequence log, creating its folder.

        Args:
            path: Where to write; None takes it from the environment.
        """
        self.path = sequence_log_path() if path is None else path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.steps = 0

    def _write(self, level: str, fields: dict[str, object]) -> None:
        """Append one line to the log.

        Args:
            level: INFO or ERROR.
            fields: What to record, in order.
        """
        stamp = datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3]
        detail = " ".join(f"{name}={value}" for name, value in fields.items())
        with self.path.open("a", encoding="utf-8") as log:
            log.write(f"{stamp} {level:5} {detail}\n")

    @contextmanager
    def step(self, action: str, instrument: str, **details: object) -> Iterator[None]:
        """Run one step of a sequence, logging what it did or why it stopped.

        Args:
            action: What the step does, for example 'verify level offset'.
            instrument: Which instrument it acts on.
            **details: The parameters the step uses, written to the log.

        Yields:
            Control, while the step runs.

        Raises:
            StepFailedError: If the step raised. The message names the step only; the reason is
                written to the log, as a sequencer does.
        """
        self.steps += 1
        number = self.steps
        # The reason is written last on purpose: it is free text that may contain '=', and a
        # reader (triage/evidence.py) takes everything after 'reason=' as one value.
        try:
            yield
        except Exception as error:
            self._write(
                "ERROR",
                {
                    "step": number,
                    "action": action,
                    "instrument": instrument,
                    **details,
                    "reason": f"{type(error).__name__}: {error}",
                },
            )
            raise StepFailedError(f"Step {number} ({action}) failed on the {instrument}") from None
        self._write(
            "INFO",
            {"step": number, "action": action, "instrument": instrument, **details, "result": "ok"},
        )
