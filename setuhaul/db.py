"""SQLite connection helper.

isolation_level=None means Python will NOT open hidden transactions for us;
the booking service opens them explicitly with BEGIN IMMEDIATE (a real write lock).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Read-only URI connection: physically cannot write the original snapshot."""
    conn = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def write_txn(conn: sqlite3.Connection):
    """One BEGIN IMMEDIATE transaction: COMMIT on success, ROLLBACK on any error.

    BEGIN IMMEDIATE takes the database write lock *before* we re-check a slot,
    so two drivers can never both see 'free' and both book it.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
