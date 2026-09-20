"""Minimal, dependency-free migration runner. Each file in migrations/ is
named `NNNN_description.sql` and applied exactly once, in order, inside a
transaction, tracked in `schema_migrations`. This is intentionally simpler
than Alembic: the schema is small enough that hand-written SQL stays
readable, and a bespoke runner means no ORM model/migration drift to reason
about in a security-sensitive codebase.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

logger = logging.getLogger(__name__)


def _migration_files() -> list[tuple[int, str, str]]:
    package = resources.files("pihole_guardian.db") / "migrations"
    out = []
    for entry in sorted(package.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".sql"):
            continue
        version = int(entry.name.split("_", 1)[0])
        out.append((version, entry.name, entry.read_text()))
    return sorted(out, key=lambda t: t[0])


def _split_statements(sql: str) -> list[str]:
    """Splits a migration file into individual statements.

    Deliberately not `conn.executescript()`: that method implicitly commits
    any pending transaction before it runs and performs no transaction
    control of its own for the rest of the script (confirmed against a real
    Python 3.14 sqlite3 module — it broke the manual BEGIN/COMMIT wrapping
    below, which is exactly the atomicity this runner exists to provide).
    Executing one statement at a time via `conn.execute()` has no such
    surprise.

    Line comments are stripped before splitting on ';' — caught by an actual
    test run: a semicolon inside a prose `-- comment` (not in any SQL string
    or trigger body) was enough to fool a naive split into cutting a CREATE
    TABLE statement in half. The migration files are plain DDL with no
    semicolons inside string literals or trigger bodies; if a future one
    needs either, this function will need to get smarter alongside it.
    """
    without_comments = "\n".join(
        line for line in (raw_line.split("--", 1)[0] for raw_line in sql.splitlines())
    )
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def run_migrations(conn: sqlite3.Connection) -> list[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied_versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    newly_applied: list[int] = []
    for version, name, sql in _migration_files():
        if version in applied_versions:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _split_statements(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, datetime.now(timezone.utc).isoformat()),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            logger.exception("Migration %s failed", name)
            raise
        newly_applied.append(version)
        logger.info("Applied migration %s", name)
    return newly_applied


def current_schema_version(database_path: Path) -> int:
    if not database_path.exists():
        return 0
    conn = sqlite3.connect(str(database_path))
    try:
        cur = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
        return cur.fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()
