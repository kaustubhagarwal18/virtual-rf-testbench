"""Tests for the history database: durable settings, atomic writes, and unusable files."""

from pathlib import Path

import pytest

from triage.history_db import Database, DatabaseError


def test_a_new_file_gets_the_tables_and_durable_settings(tmp_path: Path) -> None:
    """Opening creates every table, in write-ahead-log mode with full sync."""
    database = Database.open(tmp_path / "history.db")
    tables = {row[0] for row in database.query("SELECT name FROM sqlite_master")}
    assert {"embed_model", "vectors", "runs"} <= tables
    assert database.query("PRAGMA journal_mode") == [("wal",)]
    full_sync = 2
    assert database.query("PRAGMA synchronous") == [(full_sync,)]


def test_a_failed_transaction_writes_nothing(tmp_path: Path) -> None:
    """A block that raises rolls back every write it made."""
    database = Database.open(tmp_path / "history.db")
    with pytest.raises(RuntimeError), database.transaction() as cursor:
        cursor.execute("INSERT INTO embed_model VALUES ('m', 'd', 1.0)")
        raise RuntimeError("interrupted")
    assert database.query("SELECT COUNT(*) FROM embed_model") == [(0,)]


def test_a_committed_write_survives_reopening(tmp_path: Path) -> None:
    """What a finished transaction wrote is there for the next process."""
    path = tmp_path / "history.db"
    database = Database.open(path)
    with database.transaction() as cursor:
        cursor.execute("INSERT INTO embed_model VALUES ('m', 'd', 1.0)")
    database.close()
    assert Database.open(path).query("SELECT digest FROM embed_model") == [("d",)]


@pytest.mark.parametrize(
    "garbage",
    [b"not a database at all", b"\x00" * 512, b"SQLite format 3\x00" + b"\xff" * 200],
    ids=["text", "zeros", "bad-header"],
)
def test_an_unusable_file_is_reported_with_the_fix_and_kept(tmp_path: Path, garbage: bytes) -> None:
    """A file that isn't a usable database says to move it aside, and is never deleted."""
    path = tmp_path / "history.db"
    path.write_bytes(garbage)
    with pytest.raises(DatabaseError, match="move it aside") as raised:
        Database.open(path)
    assert str(path) in str(raised.value)
    assert path.read_bytes() == garbage


def test_a_missing_folder_is_reported_with_the_fix(tmp_path: Path) -> None:
    """A file in a folder that doesn't exist names the file and says to check the folder."""
    path = tmp_path / "absent" / "history.db"
    with pytest.raises(DatabaseError, match="folder exists") as raised:
        Database.open(path)
    assert str(path) in str(raised.value)
