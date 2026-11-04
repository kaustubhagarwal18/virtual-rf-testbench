"""JSON logging: one JSON object per line, so logs can be searched and ingested later."""

import json
import logging
import sys
from datetime import UTC, datetime

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
        return json.dumps(entry, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Send all logging to stderr as JSON lines, replacing any existing handlers.

    Args:
        level: The lowest level to log.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
