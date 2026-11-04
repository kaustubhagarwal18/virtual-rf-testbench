"""Tests for JSON logging."""

import json
import logging
import sys

from simulators.common.logs import JsonFormatter, configure_logging


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


def test_configure_logging_installs_one_json_handler() -> None:
    """configure_logging leaves the root logger with a single JSON handler."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging(logging.DEBUG)
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert root.level == logging.DEBUG
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_extra_fields_never_overwrite_the_standard_ones() -> None:
    """An extra field named like a standard one (level in dBm, say) is kept under extra_<name>."""
    entry = json.loads(JsonFormatter().format(make_record("x", level=-10.0, time="later")))
    assert entry["level"] == "WARNING"
    assert (entry["extra_level"], entry["extra_time"]) == (-10.0, "later")
