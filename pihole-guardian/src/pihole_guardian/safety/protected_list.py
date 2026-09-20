"""Manually managed protected-domain list (requirement #6).

The database table (`protected_domains`) is the single source of truth at
runtime. `config/protected_domains.yml` is a human-editable seed file:
`seed_from_file` upserts its contents into the table idempotently on every
startup, so editing the YAML and restarting is a supported workflow, but so
is adding entries live via `service allow <domain>` / the dashboard without
ever touching the file. Entries added either way behave identically.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import yaml

from pihole_guardian.db import repository
from pihole_guardian.domain_utils import is_subdomain_or_equal, matches_wildcard, normalize_domain

logger = logging.getLogger(__name__)

VALID_MATCH_TYPES = {"exact", "parent", "wildcard"}


def seed_from_file(conn: sqlite3.Connection, path: Path) -> int:
    if not path.exists():
        logger.info("No protected-domains seed file at %s; skipping", path)
        return 0
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        logger.error("Failed to parse protected-domains file %s: %s", path, exc)
        return 0

    entries = raw.get("protected", []) if isinstance(raw, dict) else raw
    count = 0
    for entry in entries or []:
        if isinstance(entry, str):
            pattern, match_type, reason = entry, "exact", None
        else:
            pattern = entry.get("pattern")
            match_type = entry.get("match_type", "exact")
            reason = entry.get("reason")
        if not pattern or match_type not in VALID_MATCH_TYPES:
            logger.warning("Skipping invalid protected-domain entry: %r", entry)
            continue
        normalized = normalize_domain(pattern.lstrip("*."))
        canonical_pattern = ("*." + normalized.domain) if match_type == "wildcard" else normalized.domain
        repository.add_protected_domain(conn, canonical_pattern, match_type, reason, source="file")
        count += 1
    return count


def is_protected(conn: sqlite3.Connection, domain: str) -> tuple[bool, str | None]:
    for row in repository.list_protected_domains(conn):
        pattern, match_type, reason = row["pattern"], row["match_type"], row["reason"]
        if match_type == "exact" and domain == pattern:
            return True, reason
        if match_type == "parent" and is_subdomain_or_equal(domain, pattern):
            return True, reason
        if match_type == "wildcard" and matches_wildcard(domain, pattern):
            return True, reason
    return False, None
