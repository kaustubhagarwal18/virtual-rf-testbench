"""The SQLite file the triage service keeps its history and embedding cache in.

One connection per process, used under one lock, in write-ahead-log mode with full sync: a
commit is on disk before the caller hears about it. Every write runs in a `BEGIN IMMEDIATE`
transaction, so a crash leaves either the whole write or none of it. The file is user data:
nothing here ever deletes it, and a file that fails its integrity check is reported with
the fix, never replaced.
"""

import contextlib
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BUSY_TIMEOUT_MS = 5000
"""How long a write waits for a lock held by another connection before giving up."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS embed_model (
    model TEXT PRIMARY KEY,
    digest TEXT NOT NULL,
    verified_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS vectors (
    model TEXT NOT NULL,
    digest TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    vector TEXT NOT NULL,
    PRIMARY KEY (model, digest, text_sha256)
);
CREATE TABLE IF NOT EXISTS runs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL UNIQUE,
    text TEXT NOT NULL,
    fault TEXT NOT NULL,
    action TEXT NOT NULL,
    fix_applied TEXT NOT NULL,
    symptom_text TEXT NOT NULL,
    label TEXT NOT NULL,
    added_at REAL NOT NULL
);
"""
"""The three tables: the embedding model's last verified digest, cached vectors keyed by
model, digest and text hash, and the runs added over HTTP in the order they were committed."""


class DatabaseError(Exception):
    """The history database can't be used; the message names the file and the fix."""


@dataclass
class Database:
    """The open history database, shared by the request threads and the loader.

    Attributes:
        path: The file.
        connection: The one connection, opened in autocommit mode so transactions are
            explicit.
        lock: Serialises every use of the connection.
    """

    path: Path
    connection: sqlite3.Connection
    lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def open(cls, path: Path) -> "Database":
        """Open the file, check its integrity, and create the tables it lacks.

        Args:
            path: The file; its folder must already exist, as a mounted volume does.

        Returns:
            The database, ready to use.

        Raises:
            DatabaseError: If the file can't be opened, or is corrupt; the message says
                which and how to fix it.
        """
        try:
            connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        except sqlite3.Error as error:
            raise DatabaseError(_cannot_open(path, error)) from error
        try:
            _prepare(connection)
        except sqlite3.OperationalError as error:
            connection.close()
            raise DatabaseError(_cannot_open(path, error)) from error
        except (sqlite3.DatabaseError, _CorruptError) as error:
            connection.close()
            raise DatabaseError(
                f"{path} is not a usable history database ({error}); move it aside and "
                "restart to begin a new one"
            ) from error
        return cls(path, connection)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        """Run writes as one transaction, committed only when the block finishes.

        Yields:
            sqlite3.Cursor: A cursor to write with; everything it does is rolled back if the
            block raises or the commit fails.

        Raises:
            DatabaseError: If SQLite fails while running the transaction, such as on a full
                disk; the message names the file and what to check. Other exceptions from the
                block carry on to the caller unchanged.
        """
        with self.lock:
            committed = False
            try:
                cursor = self.connection.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                yield cursor
                cursor.execute("COMMIT")
                committed = True
            except sqlite3.Error as error:
                raise DatabaseError(_failed(self.path, error)) from error
            finally:
                if not committed:
                    self._roll_back()

    def query(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        """Read rows.

        Args:
            sql: The SELECT statement, with ? placeholders.
            parameters: The values for the placeholders.

        Returns:
            Every row it returned.

        Raises:
            DatabaseError: If SQLite fails to read, such as on an I/O error; the message
                names the file and what to check.
        """
        with self.lock:
            try:
                return list(self.connection.execute(sql, parameters).fetchall())
            except sqlite3.Error as error:
                raise DatabaseError(_failed(self.path, error)) from error

    def _roll_back(self) -> None:
        """Undo an unfinished transaction; one SQLite already ended needs nothing undone.

        A connection that has failed can't even say whether a transaction is open; SQLite
        then rolls back on its own, so there is nothing more to do and nothing to report
        beyond the error already on its way to the caller.
        """
        with contextlib.suppress(sqlite3.Error):
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")

    def close(self) -> None:
        """Close the connection."""
        with self.lock:
            self.connection.close()


class _CorruptError(Exception):
    """The integrity check found a problem in a file SQLite could otherwise read."""


def _prepare(connection: sqlite3.Connection) -> None:
    """Set the durability settings, check integrity, and create missing tables.

    Args:
        connection: A fresh connection.

    Raises:
        _CorruptError: If PRAGMA quick_check reports anything but ok.
    """
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA foreign_keys = ON")
    check = [row[0] for row in connection.execute("PRAGMA quick_check").fetchall()]
    if check != ["ok"]:
        raise _CorruptError("; ".join(str(line) for line in check[:3]))
    connection.executescript(SCHEMA)


def _failed(path: Path, error: Exception) -> str:
    """Say that a read or write on an open database failed, and what to check.

    Args:
        path: The file.
        error: What SQLite reported.

    Returns:
        One line naming the file and the fix.
    """
    return (
        f"the history database {path} failed ({error}); check the disk has space and the "
        "file is writable"
    )


def _cannot_open(path: Path, error: Exception) -> str:
    """Say why the file couldn't be opened, and what to check.

    Args:
        path: The file.
        error: What SQLite reported.

    Returns:
        One line naming the file and the fix.
    """
    return (
        f"can't open the history database {path} ({error}); check that its folder exists "
        "and is writable (a mounted volume, in a container)"
    )
