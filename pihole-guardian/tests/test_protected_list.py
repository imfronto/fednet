from pihole_guardian.db import repository
from pihole_guardian.safety import protected_list


def test_exact_match_only_matches_exact_domain(conn):
    repository.add_protected_domain(conn, "example.com", "exact", "test")
    assert protected_list.is_protected(conn, "example.com")[0] is True
    assert protected_list.is_protected(conn, "sub.example.com")[0] is False


def test_parent_match_covers_subdomains(conn):
    repository.add_protected_domain(conn, "example.com", "parent", "test")
    assert protected_list.is_protected(conn, "example.com")[0] is True
    assert protected_list.is_protected(conn, "deep.sub.example.com")[0] is True
    assert protected_list.is_protected(conn, "notexample.com")[0] is False


def test_wildcard_match_excludes_apex(conn):
    repository.add_protected_domain(conn, "*.example.com", "wildcard", "test")
    assert protected_list.is_protected(conn, "sub.example.com")[0] is True
    assert protected_list.is_protected(conn, "example.com")[0] is False


def test_seed_from_file_is_idempotent(conn, tmp_path):
    seed = tmp_path / "protected.yml"
    seed.write_text(
        "protected:\n"
        "  - pattern: microsoft.com\n"
        "    match_type: parent\n"
        "    reason: OS updates\n"
        "  - apple.com\n"
    )
    count1 = protected_list.seed_from_file(conn, seed)
    count2 = protected_list.seed_from_file(conn, seed)
    assert count1 == 2
    assert count2 == 2
    rows = repository.list_protected_domains(conn)
    assert len(rows) == 2  # no duplicates from re-seeding


def test_missing_seed_file_does_not_error(conn, tmp_path):
    assert protected_list.seed_from_file(conn, tmp_path / "does_not_exist.yml") == 0


def test_unprotect_removes_entry_regardless_of_source(conn):
    repository.add_protected_domain(conn, "example.com", "exact", "test", source="file")
    assert repository.remove_protected_domain(conn, "example.com") is True
    assert protected_list.is_protected(conn, "example.com")[0] is False
