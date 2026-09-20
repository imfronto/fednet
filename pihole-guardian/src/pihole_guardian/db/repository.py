"""Data-access layer. Plain functions over an `sqlite3.Connection` rather
than a repository *class* — there's no polymorphism need here (SQLite is
the only backend), so a class would just be ceremony around functions that
all take `conn` as their first argument.

Note on query construction: every value from a caller goes through `?`
parameter binding. The only place a query string is assembled from parts is
`list_domains`, where the "parts" are a fixed set of column="?"" clauses this
module hard-codes (never caller-supplied text) — no user-controlled string
ever becomes part of the SQL text itself.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from pihole_guardian.db.models import Change, Classification, Decision, Domain, Evidence
from pihole_guardian.domain_utils import NormalizedDomain
from pihole_guardian.time_utils import now_iso

# --- domains ---------------------------------------------------------------


def upsert_domain_seen(
    conn: sqlite3.Connection,
    normalized: NormalizedDomain,
    query_delta: int = 1,
) -> Domain:
    ts = now_iso()
    existing = conn.execute("SELECT * FROM domains WHERE domain = ?", (normalized.domain,)).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO domains (domain, unicode_domain, registrable_domain, first_seen, last_seen, "
            "query_count, distinct_client_count, pihole_status, pihole_list_managed_by, active, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, 'unlisted', NULL, 1, ?, ?)",
            (
                normalized.domain,
                normalized.unicode,
                normalized.registrable_domain,
                ts,
                ts,
                query_delta,
                ts,
                ts,
            ),
        )
    else:
        conn.execute(
            "UPDATE domains SET last_seen = ?, query_count = query_count + ?, active = 1, updated_at = ? "
            "WHERE id = ?",
            (ts, query_delta, ts, existing["id"]),
        )
    row = conn.execute("SELECT * FROM domains WHERE domain = ?", (normalized.domain,)).fetchone()
    return Domain.from_row(row)


def get_domain(conn: sqlite3.Connection, domain: str) -> Domain | None:
    row = conn.execute("SELECT * FROM domains WHERE domain = ?", (domain,)).fetchone()
    return Domain.from_row(row) if row else None


def get_domain_by_id(conn: sqlite3.Connection, domain_id: int) -> Domain | None:
    row = conn.execute("SELECT * FROM domains WHERE id = ?", (domain_id,)).fetchone()
    return Domain.from_row(row) if row else None


def set_distinct_client_count(conn: sqlite3.Connection, domain_id: int, count: int) -> None:
    conn.execute(
        "UPDATE domains SET distinct_client_count = ?, updated_at = ? WHERE id = ?",
        (count, now_iso(), domain_id),
    )


def set_pihole_status(
    conn: sqlite3.Connection, domain_id: int, status: str, managed_by: str | None
) -> None:
    conn.execute(
        "UPDATE domains SET pihole_status = ?, pihole_list_managed_by = ?, updated_at = ? WHERE id = ?",
        (status, managed_by, now_iso(), domain_id),
    )


def mark_inactive_domains(conn: sqlite3.Connection, older_than_days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    cur = conn.execute("UPDATE domains SET active = 0 WHERE last_seen < ? AND active = 1", (cutoff,))
    return cur.rowcount


# Fixed, hard-coded column filters only — never built from caller-supplied text.
_DOMAIN_FILTER_COLUMNS = {"status": "pihole_status = ?", "managed_by": "pihole_list_managed_by = ?"}


def list_domains(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    managed_by: str | None = None,
    active_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[Domain]:
    clauses = []
    params: list = []
    if status:
        clauses.append(_DOMAIN_FILTER_COLUMNS["status"])
        params.append(status)
    if managed_by:
        clauses.append(_DOMAIN_FILTER_COLUMNS["managed_by"])
        params.append(managed_by)
    if active_only:
        clauses.append("active = 1")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.extend([limit, offset])
    sql = "SELECT * FROM domains " + where + " ORDER BY last_seen DESC LIMIT ? OFFSET ?"
    rows = conn.execute(sql, params).fetchall()
    return [Domain.from_row(r) for r in rows]


def domains_needing_classification(conn: sqlite3.Connection, max_age_hours: int, limit: int) -> list[Domain]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    rows = conn.execute(
        "SELECT d.* FROM domains d LEFT JOIN "
        "(SELECT domain_id, MAX(created_at) AS latest FROM classifications GROUP BY domain_id) c "
        "ON c.domain_id = d.id "
        "WHERE d.active = 1 AND (c.latest IS NULL OR c.latest < ?) "
        "ORDER BY (c.latest IS NOT NULL), d.last_seen DESC LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    return [Domain.from_row(r) for r in rows]


def search_domains(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[Domain]:
    rows = conn.execute(
        "SELECT * FROM domains WHERE domain LIKE ? ORDER BY last_seen DESC LIMIT ?",
        (f"%{query}%", limit),
    ).fetchall()
    return [Domain.from_row(r) for r in rows]


# --- observations (daily rollups) ------------------------------------------


def record_daily_observation(
    conn: sqlite3.Connection,
    domain_id: int,
    observed_date: str,
    *,
    query_delta: int = 0,
    nxdomain_delta: int = 0,
    servfail_delta: int = 0,
    blocked_delta: int = 0,
    ok_delta: int = 0,
    record_types: set[str] | None = None,
) -> None:
    existing = conn.execute(
        "SELECT * FROM observations WHERE domain_id = ? AND observed_date = ?", (domain_id, observed_date)
    ).fetchone()
    types_list = sorted(record_types) if record_types else []
    if existing is None:
        conn.execute(
            "INSERT INTO observations (domain_id, observed_date, query_count, nxdomain_count, "
            "servfail_count, blocked_count, ok_count, distinct_clients, record_types) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                domain_id,
                observed_date,
                query_delta,
                nxdomain_delta,
                servfail_delta,
                blocked_delta,
                ok_delta,
                json.dumps(types_list),
            ),
        )
    else:
        merged_types = sorted(set(json.loads(existing["record_types"] or "[]")) | (record_types or set()))
        conn.execute(
            "UPDATE observations SET query_count = query_count + ?, nxdomain_count = nxdomain_count + ?, "
            "servfail_count = servfail_count + ?, blocked_count = blocked_count + ?, "
            "ok_count = ok_count + ?, record_types = ? WHERE id = ?",
            (
                query_delta,
                nxdomain_delta,
                servfail_delta,
                blocked_delta,
                ok_delta,
                json.dumps(merged_types),
                existing["id"],
            ),
        )


def purge_old_observations(conn: sqlite3.Connection, older_than_days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).strftime("%Y-%m-%d")
    cur = conn.execute("DELETE FROM observations WHERE observed_date < ?", (cutoff,))
    return cur.rowcount


# --- recent buckets (fine-grained, short retention, disruption detection) --


def bump_recent_bucket(
    conn: sqlite3.Connection,
    domain_id: int,
    bucket_start_iso: str,
    *,
    ok: int = 0,
    nxdomain: int = 0,
    servfail: int = 0,
    blocked: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO recent_buckets (domain_id, bucket_start, ok_count, nxdomain_count, servfail_count, "
        "blocked_count) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(domain_id, bucket_start) DO UPDATE SET "
        "ok_count = ok_count + excluded.ok_count, "
        "nxdomain_count = nxdomain_count + excluded.nxdomain_count, "
        "servfail_count = servfail_count + excluded.servfail_count, "
        "blocked_count = blocked_count + excluded.blocked_count",
        (domain_id, bucket_start_iso, ok, nxdomain, servfail, blocked),
    )


def recent_bucket_totals(
    conn: sqlite3.Connection, domain_id: int, since_iso: str, until_iso: str | None = None
) -> dict:
    if until_iso:
        rows = conn.execute(
            "SELECT * FROM recent_buckets WHERE domain_id = ? AND bucket_start >= ? AND bucket_start < ?",
            (domain_id, since_iso, until_iso),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM recent_buckets WHERE domain_id = ? AND bucket_start >= ?", (domain_id, since_iso)
        ).fetchall()
    totals = {"ok": 0, "nxdomain": 0, "servfail": 0, "blocked": 0}
    for r in rows:
        totals["ok"] += r["ok_count"]
        totals["nxdomain"] += r["nxdomain_count"]
        totals["servfail"] += r["servfail_count"]
        totals["blocked"] += r["blocked_count"]
    return totals


def purge_old_recent_buckets(conn: sqlite3.Connection, older_than_hours: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
    cur = conn.execute("DELETE FROM recent_buckets WHERE bucket_start < ?", (cutoff,))
    return cur.rowcount


# --- client_seen (optional, privacy-gated) ----------------------------------


def record_client_seen(conn: sqlite3.Connection, domain_id: int, client_hash: str) -> None:
    ts = now_iso()
    conn.execute(
        "INSERT INTO client_seen (domain_id, client_hash, first_seen, last_seen) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(domain_id, client_hash) DO UPDATE SET last_seen = excluded.last_seen",
        (domain_id, client_hash, ts, ts),
    )


def distinct_client_count(conn: sqlite3.Connection, domain_id: int) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM client_seen WHERE domain_id = ?", (domain_id,)).fetchone()
    return row["c"]


# --- evidence ----------------------------------------------------------------


def add_evidence(
    conn: sqlite3.Connection,
    domain_id: int,
    source: str,
    verdict: str,
    weight: float,
    *,
    category: str | None = None,
    detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO evidence (domain_id, source, category, verdict, weight, detail, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (domain_id, source, category, verdict, weight, detail, now_iso()),
    )


def latest_evidence(conn: sqlite3.Connection, domain_id: int, max_age_hours: int | None = None) -> list[Evidence]:
    if max_age_hours is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        rows = conn.execute(
            "SELECT * FROM evidence WHERE domain_id = ? AND observed_at >= ? ORDER BY observed_at DESC",
            (domain_id, cutoff),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM evidence WHERE domain_id = ? ORDER BY observed_at DESC", (domain_id,)
        ).fetchall()
    return [Evidence.from_row(r) for r in rows]


# --- classifications -----------------------------------------------------


def add_classification(
    conn: sqlite3.Connection,
    domain_id: int,
    category: str,
    confidence: float,
    legitimate_signals: list[str],
    malicious_signals: list[str],
    conflict: bool,
    reasoning: str,
) -> Classification:
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO classifications (domain_id, category, confidence, legitimate_signals, "
        "malicious_signals, conflict, reasoning, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            domain_id,
            category,
            confidence,
            json.dumps(legitimate_signals),
            json.dumps(malicious_signals),
            int(conflict),
            reasoning,
            ts,
        ),
    )
    row = conn.execute("SELECT * FROM classifications WHERE id = ?", (cur.lastrowid,)).fetchone()
    return Classification.from_row(row)


def latest_classification(conn: sqlite3.Connection, domain_id: int) -> Classification | None:
    row = conn.execute(
        "SELECT * FROM classifications WHERE domain_id = ? ORDER BY created_at DESC LIMIT 1", (domain_id,)
    ).fetchone()
    return Classification.from_row(row) if row else None


# --- decisions ---------------------------------------------------------------


def add_decision(
    conn: sqlite3.Connection,
    domain_id: int,
    classification_id: int | None,
    action: str,
    confidence: float,
    reason: str,
    mode: str,
    status: str = "pending",
) -> Decision:
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO decisions (domain_id, classification_id, action, confidence, reason, mode, status, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (domain_id, classification_id, action, confidence, reason, mode, status, ts),
    )
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (cur.lastrowid,)).fetchone()
    return Decision.from_row(row)


def resolve_decision(conn: sqlite3.Connection, decision_id: int, status: str, resolved_by: str) -> None:
    conn.execute(
        "UPDATE decisions SET status = ?, resolved_at = ?, resolved_by = ? WHERE id = ?",
        (status, now_iso(), resolved_by, decision_id),
    )


def get_decision(conn: sqlite3.Connection, decision_id: int) -> Decision | None:
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    return Decision.from_row(row) if row else None


def pending_decisions(conn: sqlite3.Connection, action: str | None = None) -> list[Decision]:
    if action:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE status = 'pending' AND action = ? ORDER BY created_at", (action,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
    return [Decision.from_row(r) for r in rows]


def latest_decision(conn: sqlite3.Connection, domain_id: int) -> Decision | None:
    row = conn.execute(
        "SELECT * FROM decisions WHERE domain_id = ? ORDER BY created_at DESC LIMIT 1", (domain_id,)
    ).fetchone()
    return Decision.from_row(row) if row else None


# --- changes (rollback ledger) ----------------------------------------------


def record_change(
    conn: sqlite3.Connection,
    change_id: str,
    domain_id: int,
    decision_id: int | None,
    change_type: str,
    previous_state: dict,
    new_state: dict,
    actor: str,
    simulated: bool = False,
) -> Change:
    ts = now_iso()
    conn.execute(
        "INSERT INTO changes (id, domain_id, decision_id, change_type, previous_state, new_state, "
        "simulated, applied, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
        (
            change_id,
            domain_id,
            decision_id,
            change_type,
            json.dumps(previous_state),
            json.dumps(new_state),
            int(simulated),
            actor,
            ts,
        ),
    )
    return get_change(conn, change_id)  # type: ignore[return-value]


def mark_change_applied(conn: sqlite3.Connection, change_id: str, error: str | None = None) -> None:
    ts = now_iso()
    conn.execute(
        "UPDATE changes SET applied = ?, applied_at = ?, error = ? WHERE id = ?",
        (0 if error else 1, ts, error, change_id),
    )


def mark_change_rolled_back(conn: sqlite3.Connection, change_id: str) -> None:
    conn.execute(
        "UPDATE changes SET rolled_back = 1, rolled_back_at = ? WHERE id = ?", (now_iso(), change_id)
    )


def get_change(conn: sqlite3.Connection, change_id: str) -> Change | None:
    row = conn.execute("SELECT * FROM changes WHERE id = ?", (change_id,)).fetchone()
    return Change.from_row(row) if row else None


def list_changes(
    conn: sqlite3.Connection, *, domain_id: int | None = None, limit: int = 100, offset: int = 0
) -> list[Change]:
    if domain_id is not None:
        rows = conn.execute(
            "SELECT * FROM changes WHERE domain_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (domain_id, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM changes ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
    return [Change.from_row(r) for r in rows]


def purge_old_changes(conn: sqlite3.Connection, older_than_days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    # Never purge un-applied or non-rolled-back changes even if old — the audit
    # trail requirement (#18) takes priority over storage savings; only prune
    # changes that are both applied and old, and never touch changes tied to a
    # currently auto-managed domain (those must stay explainable via `explain`).
    cur = conn.execute(
        "DELETE FROM changes WHERE created_at < ? AND applied = 1 AND id NOT IN "
        "(SELECT source_change_id FROM auto_managed_domains WHERE source_change_id IS NOT NULL)",
        (cutoff,),
    )
    return cur.rowcount


# --- protected domains -------------------------------------------------------


def add_protected_domain(
    conn: sqlite3.Connection, pattern: str, match_type: str, reason: str | None, source: str = "manual"
) -> None:
    ts = now_iso()
    conn.execute(
        "INSERT INTO protected_domains (pattern, match_type, reason, source, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(pattern) DO UPDATE SET reason = excluded.reason, updated_at = excluded.updated_at",
        (pattern, match_type, reason, source, ts, ts),
    )


def remove_protected_domain(conn: sqlite3.Connection, pattern: str) -> bool:
    # Deliberately not filtered by `source` — an explicit `service unprotect`
    # is always a human decision, regardless of whether the entry originally
    # came from the seed file or a prior CLI call. Note this does not edit
    # the seed file itself: if the pattern is still listed there, it will
    # reappear on the next restart (seeding is additive, never destructive —
    # see protected_list.seed_from_file).
    cur = conn.execute("DELETE FROM protected_domains WHERE pattern = ?", (pattern,))
    return cur.rowcount > 0


def list_protected_domains(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM protected_domains ORDER BY pattern").fetchall()


# --- auto-managed domains -----------------------------------------------------


def upsert_auto_managed(
    conn: sqlite3.Connection,
    domain_id: int,
    classification: str,
    confidence: float,
    evidence_summary: str,
    reason: str,
    source: str,
    source_change_id: str | None,
) -> None:
    ts = now_iso()
    existing = conn.execute(
        "SELECT domain_id FROM auto_managed_domains WHERE domain_id = ?", (domain_id,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE auto_managed_domains SET classification = ?, confidence = ?, evidence_summary = ?, "
            "last_verified = ?, last_seen = ?, reason = ?, source = ?, source_change_id = ? "
            "WHERE domain_id = ?",
            (classification, confidence, evidence_summary, ts, ts, reason, source, source_change_id, domain_id),
        )
    else:
        conn.execute(
            "INSERT INTO auto_managed_domains (domain_id, classification, confidence, evidence_summary, "
            "date_added, last_verified, last_seen, reason, source, source_change_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (domain_id, classification, confidence, evidence_summary, ts, ts, ts, reason, source, source_change_id),
        )


def remove_auto_managed(conn: sqlite3.Connection, domain_id: int) -> None:
    conn.execute("DELETE FROM auto_managed_domains WHERE domain_id = ?", (domain_id,))


def get_auto_managed(conn: sqlite3.Connection, domain_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM auto_managed_domains WHERE domain_id = ?", (domain_id,)).fetchone()


def list_auto_managed(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT am.*, d.domain FROM auto_managed_domains am JOIN domains d ON d.id = am.domain_id "
        "ORDER BY am.date_added DESC"
    ).fetchall()


def touch_auto_managed_last_seen(conn: sqlite3.Connection, domain_id: int) -> None:
    conn.execute(
        "UPDATE auto_managed_domains SET last_seen = ? WHERE domain_id = ?", (now_iso(), domain_id)
    )


# --- incidents -----------------------------------------------------------------


def create_incident(
    conn: sqlite3.Connection,
    domain_id: int,
    change_id: str | None,
    evidence: dict,
    auto_unblocked: bool,
) -> int:
    cur = conn.execute(
        "INSERT INTO incidents (domain_id, change_id, evidence, auto_unblocked, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (domain_id, change_id, json.dumps(evidence), int(auto_unblocked), now_iso()),
    )
    return cur.lastrowid  # type: ignore[return-value]


def list_incidents(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT i.*, d.domain FROM incidents i JOIN domains d ON d.id = i.domain_id "
            "WHERE i.status = ? ORDER BY i.created_at DESC",
            (status,),
        ).fetchall()
    return conn.execute(
        "SELECT i.*, d.domain FROM incidents i JOIN domains d ON d.id = i.domain_id ORDER BY i.created_at DESC"
    ).fetchall()


def has_open_incident(conn: sqlite3.Connection, domain_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM incidents WHERE domain_id = ? AND status = 'open' LIMIT 1", (domain_id,)
    ).fetchone()
    return row is not None


def resolve_incident(conn: sqlite3.Connection, incident_id: int, status: str) -> None:
    conn.execute(
        "UPDATE incidents SET status = ?, resolved_at = ? WHERE id = ?", (status, now_iso(), incident_id)
    )


def domains_sharing_registrable(
    conn: sqlite3.Connection, registrable_domain: str, exclude_domain_id: int
) -> list[Domain]:
    rows = conn.execute(
        "SELECT * FROM domains WHERE registrable_domain = ? AND id != ?",
        (registrable_domain, exclude_domain_id),
    ).fetchall()
    return [Domain.from_row(r) for r in rows]


# --- configuration (runtime overrides) --------------------------------------


def set_config(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO configuration (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now_iso()),
    )


def get_config(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM configuration WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
