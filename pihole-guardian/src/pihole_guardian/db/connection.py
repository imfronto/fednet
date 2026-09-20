"""SQLite connection handling.

Each caller gets its own short-lived connection (SQLite handles concurrent
readers fine, and WAL mode lets one writer and many readers coexist without
callers needing to coordinate). `busy_timeout` absorbs the brief contention
that still happens when the scheduler thread and an API request write at
close to the same moment, rather than surfacing a `database is locked` error.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(database_path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction wrapper. Required because isolation_level=None
    (autocommit) is used above, so multi-statement changes need an explicit
    BEGIN/COMMIT to stay atomic — most importantly the change-ledger writes
    in safety/change_manager.py, which must never be left half-applied.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
