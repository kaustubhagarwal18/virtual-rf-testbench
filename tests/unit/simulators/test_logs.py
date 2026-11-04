"""Tests for JSON logging."""

import io
import json
import logging
import math
import sys
import threading
import time
from typing import Any

from simulators.common.logs import DroppingQueueHandler, JsonFormatter, json_logging
from tests.utilities.strict_json import strict_loads


def make_record(message: str, **extra: object) -> logging.LogRecord:
    """Build a log record the way logging.warning(message, extra=...) would."""
    record = logging.LogRecord("simulators.test", logging.WARNING, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_record_becomes_one_json_object() -> None:
    """A log record is written as a single line of JSON with time, level, logger and message."""
    line = JsonFormatter().format(make_record("Command rejected"))
    entry = json.loads(line)
    assert "\n" not in line
    assert (entry["level"], entry["logger"], entry["message"]) == (
        "WARNING",
        "simulators.test",
        "Command rejected",
    )
    assert entry["time"].endswith("+00:00")


def test_extra_fields_are_included() -> None:
    """Fields passed with extra= appear as their own JSON keys."""
    entry = json.loads(JsonFormatter().format(make_record("x", command="BOG 1", peer="127.0.0.1")))
    assert (entry["command"], entry["peer"]) == ("BOG 1", "127.0.0.1")


def test_exception_traceback_is_included() -> None:
    """A logged exception carries its traceback in an 'exception' field."""
    try:
        raise RuntimeError("bug")
    except RuntimeError:
        record = logging.LogRecord(
            "simulators.test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
        entry = json.loads(JsonFormatter().format(record))
        assert "RuntimeError: bug" in entry["exception"]


def test_json_logging_writes_json_lines_and_restores_the_handlers() -> None:
    """Records reach the stream as JSON lines, tracebacks included, and the old handlers return."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    stream = io.StringIO()
    with json_logging(logging.DEBUG, stream=stream):
        assert root.level == logging.DEBUG
        logging.getLogger("simulators.test").warning("Command rejected", extra={"command": "BOG"})
        try:
            raise RuntimeError("bug")
        except RuntimeError:
            logging.getLogger("simulators.test").exception("Handler failed")
    entries = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [entry["message"] for entry in entries] == ["Command rejected", "Handler failed"]
    assert entries[0]["command"] == "BOG"
    assert "RuntimeError: bug" in entries[1]["exception"]
    assert (root.handlers, root.level) == (saved_handlers, saved_level)


RECORDS_LOGGED_WHILE_STALLED = 50
"""How many records the stalled-stream tests log: ten times the queue size they use."""


class StalledStream(io.StringIO):
    """A stream whose writes wait until the test releases it, like a pipe nobody reads."""

    def __init__(self) -> None:
        """Start stalled."""
        super().__init__()
        self.released = threading.Event()

    def write(self, text: str) -> int:
        """Wait for the release, then keep the text.

        Args:
            text: What the handler writes.

        Returns:
            The number of characters written.
        """
        self.released.wait()
        return super().write(text)


def log_while_stalled(stream: StalledStream, handler: DroppingQueueHandler, count: int) -> None:
    """Log records while nothing reads the stream, checking that logging never waits.

    Args:
        stream: The stalled stream.
        handler: The handler json_logging installed.
        count: How many records to log.
    """
    logger = logging.getLogger("simulators.test")
    started_s = time.monotonic()
    for index in range(count):
        logger.warning("Command rejected", extra={"index": index})
    assert time.monotonic() - started_s < 1.0
    assert handler.dropped > 0
    stream.released.set()


def split_drop_warnings(stream: StalledStream) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the stream's JSON lines, separating drop warnings from the other records.

    Args:
        stream: The stream after logging stopped.

    Returns:
        The drop warnings, and the other records.
    """
    entries = [json.loads(line) for line in stream.getvalue().splitlines()]
    is_warning = [entry["message"].startswith("Log records dropped") for entry in entries]
    return (
        [entry for entry, warning in zip(entries, is_warning, strict=True) if warning],
        [entry for entry, warning in zip(entries, is_warning, strict=True) if not warning],
    )


def test_a_stalled_stream_never_blocks_logging_and_drops_are_reported_once_there_is_room() -> None:
    """Logging returns at once while the stream stalls; when it drains, the drops are reported."""
    stream = StalledStream()
    with json_logging(stream=stream, queue_size=5) as handler:
        log_while_stalled(stream, handler, count=RECORDS_LOGGED_WHILE_STALLED)
        deadline_s = time.monotonic() + 5.0
        while not handler.records.empty() and time.monotonic() < deadline_s:
            time.sleep(0.01)
        logging.getLogger("simulators.test").warning("Reader is back")
        assert handler.dropped == 0
    warnings, written = split_drop_warnings(stream)
    assert len(warnings) == 1
    assert len(written) + warnings[0]["dropped_records"] == RECORDS_LOGGED_WHILE_STALLED + 1
    assert written[-1]["message"] == "Reader is back"


def test_drops_still_unreported_when_logging_stops_are_reported_then() -> None:
    """Records dropped just before shutdown are still counted in a final warning."""
    stream = StalledStream()
    with json_logging(stream=stream, queue_size=5) as handler:
        log_while_stalled(stream, handler, count=RECORDS_LOGGED_WHILE_STALLED)
    warnings, written = split_drop_warnings(stream)
    dropped = sum(warning["dropped_records"] for warning in warnings)
    assert len(written) + dropped == RECORDS_LOGGED_WHILE_STALLED
    assert warnings


def test_non_finite_extra_fields_stay_standard_json() -> None:
    """A NaN or infinite extra field is written as a string, so strict JSON readers accept it."""
    line = JsonFormatter().format(make_record("x", level_dbm=math.nan, margin_db=-math.inf))
    entry = strict_loads(line)
    assert (entry["level_dbm"], entry["margin_db"]) == ("NaN", "-Infinity")


def test_extra_fields_never_overwrite_the_standard_ones() -> None:
    """An extra field named like a standard one (level in dBm, say) is kept under extra_<name>."""
    entry = json.loads(JsonFormatter().format(make_record("x", level=-10.0, time="later")))
    assert entry["level"] == "WARNING"
    assert (entry["extra_level"], entry["extra_time"]) == (-10.0, "later")
