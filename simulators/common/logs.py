"""JSON logging: one JSON object per line, so logs can be searched and ingested later.

Logging never blocks the simulator. Each record is formatted where it is logged and put on a
bounded queue without waiting; a background thread writes the queue to stderr. If nothing
reads stderr and the queue fills, records are dropped and counted, and a warning saying how
many were lost is logged as soon as there is room again. A stalled log reader therefore
can't stall command handling or the event loop.
"""

import logging
import logging.handlers
import queue
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TextIO

from simulators.common.jsontext import to_json

LOG_QUEUE_SIZE = 10_000
"""How many log records may wait for the writer thread before new ones are dropped."""

_STANDARD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None))
) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Formats each log record as one line of JSON, including any extra= fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Turn a record into a JSON line.

        Args:
            record: The record to format.

        Returns:
            JSON with time (UTC), level, logger, message, any extra fields and, when an
            exception was logged, its traceback. An extra field named like a standard one is
            kept as extra_<name>, so it can't overwrite the real time, level or logger.
            NaN and infinities are written as strings, so the line stays standard JSON.
        """
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRIBUTES:
                entry[f"extra_{key}" if key in entry else key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return to_json(entry, stringify_unknown=True)


class DroppingQueueHandler(logging.handlers.QueueHandler):
    """Puts formatted records on a queue without ever waiting; drops them once it holds too many.

    The handler enforces the limit rather than the queue, so the writer thread's stop marker
    always fits, even when the queue is full of records.

    Attributes:
        records: The queue the writer thread reads.
        capacity: How many records may wait in it.
        dropped: How many records were dropped since the last drop warning was queued.
    """

    def __init__(self, log_queue: "queue.Queue[logging.LogRecord]", capacity: int) -> None:
        """Create the handler, formatting records as JSON before they are queued.

        Args:
            log_queue: The queue the writer thread reads.
            capacity: How many records may wait in it.
        """
        super().__init__(log_queue)
        self.setFormatter(JsonFormatter())
        self.records = log_queue
        self.capacity = capacity
        self.dropped = 0

    def enqueue(self, record: logging.LogRecord) -> None:
        """Queue a record, first queueing a warning about any records dropped before it.

        Args:
            record: The prepared record, whose message is already the JSON line.
        """
        if self.dropped and self._try_put(self._drop_warning()):
            self.dropped = 0
        if not self._try_put(record):
            self.dropped += 1

    def report_drops(self) -> None:
        """Queue the drop warning now, whatever the queue holds; used once logging stops."""
        if self.dropped:
            self.records.put_nowait(self._drop_warning())
            self.dropped = 0

    def _try_put(self, record: logging.LogRecord) -> bool:
        """Put a record on the queue if there is room.

        Args:
            record: The record to queue.

        Returns:
            Whether it was queued.
        """
        if self.records.qsize() >= self.capacity:
            return False
        self.records.put_nowait(record)
        return True

    def _drop_warning(self) -> logging.LogRecord:
        """Build the warning that says how many records were dropped.

        Returns:
            A record whose message is the JSON line of that warning.
        """
        warning = logging.LogRecord(
            __name__,
            logging.WARNING,
            __file__,
            0,
            "Log records dropped: nothing was reading the log stream",
            None,
            None,
        )
        warning.dropped_records = self.dropped
        prepared: logging.LogRecord = self.prepare(warning)
        return prepared


@contextmanager
def json_logging(
    level: int = logging.INFO, stream: TextIO | None = None, queue_size: int = LOG_QUEUE_SIZE
) -> Iterator[DroppingQueueHandler]:
    """Send all logging to a stream as JSON lines, replacing any existing handlers.

    On exit any drops not yet reported are logged, the writer thread writes out everything
    still queued, and the previous handlers and level are put back.

    Args:
        level: The lowest level to log.
        stream: Where the lines go; None means stderr.
        queue_size: How many records may wait to be written before new ones are dropped.

    Yields:
        DroppingQueueHandler: The handler the loggers write to, whose dropped count tests can
        read.
    """
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
    handler = DroppingQueueHandler(log_queue, capacity=queue_size)
    writer = logging.StreamHandler(stream or sys.stderr)
    writer.setFormatter(logging.Formatter("%(message)s"))
    listener = logging.handlers.QueueListener(log_queue, writer)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    root.handlers[:] = [handler]
    root.setLevel(level)
    listener.start()
    try:
        yield handler
    finally:
        root.handlers[:] = saved_handlers
        handler.report_drops()
        listener.stop()
        root.setLevel(saved_level)
