from pihole_guardian.db.connection import connect
from pihole_guardian.db.migrator import current_schema_version, run_migrations


def test_migrations_create_expected_tables(settings):
    c = connect(settings.database_path)
    try:
        run_migrations(c)
        tables = {
            row["name"]
            for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for expected in (
            "domains", "observations", "recent_buckets", "client_seen", "evidence", "classifications",
            "decisions", "changes", "protected_domains", "auto_managed_domains", "incidents",
            "configuration", "schema_migrations",
        ):
            assert expected in tables, f"missing table: {expected}"
    finally:
        c.close()


def test_migrations_are_idempotent(settings):
    c = connect(settings.database_path)
    try:
        first = run_migrations(c)
        second = run_migrations(c)
        assert first  # something was applied the first time
        assert second == []  # nothing left to apply the second time
    finally:
        c.close()


def test_current_schema_version_reports_correctly(settings):
    assert current_schema_version(settings.database_path) == 0  # no file yet
    c = connect(settings.database_path)
    run_migrations(c)
    c.close()
    assert current_schema_version(settings.database_path) >= 1
