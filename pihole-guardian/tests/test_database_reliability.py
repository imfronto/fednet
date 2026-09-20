"""Requirement #17/#19: database errors must be tolerated, not fatal."""
import sqlite3

import pytest

from pihole_guardian.db.connection import connect, transaction


def test_connect_enables_wal_and_busy_timeout(settings):
    conn = connect(settings.database_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 30000
    finally:
        conn.close()


def test_transaction_rolls_back_on_error(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _test_rollback (id INTEGER PRIMARY KEY, val TEXT)"
    )
    with pytest.raises(RuntimeError):
        with transaction(conn):
            conn.execute("INSERT INTO _test_rollback (val) VALUES ('x')")
            raise RuntimeError("simulated failure mid-transaction")
    count = conn.execute("SELECT COUNT(*) FROM _test_rollback").fetchone()[0]
    assert count == 0  # the insert was rolled back, not partially applied


def test_foreign_key_violation_is_rejected_not_silently_ignored(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO observations (domain_id, observed_date, query_count) VALUES (99999, '2025-01-01', 1)"
        )
