"""Shared fixtures for the triage tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from triage.history_db import Database


@pytest.fixture(autouse=True)
def close_history_databases(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Close every history database a test opened, as a process exiting would."""
    opened: list[Database] = []
    open_database = Database.open

    def recording(cls: type[Database], path: Path) -> Database:
        """Open the database as usual, and remember it."""
        del cls
        database = open_database(path)
        opened.append(database)
        return database

    monkeypatch.setattr(Database, "open", classmethod(recording))
    yield
    for database in opened:
        database.close()
