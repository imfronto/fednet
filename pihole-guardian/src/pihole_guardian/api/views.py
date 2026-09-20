"""Presentation-layer queries shared by the JSON API and the HTML dashboard
— specifically the dashboard's required filter set (All / Blocked / Allowed
/ Unknown / Needs Review / Recently Changed / High Risk). Kept out of
db/repository.py because these are read-only view compositions over several
tables for display purposes, not primitives the rest of the app needs.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from pihole_guardian.classification.categories import NEGATIVE_CATEGORIES
from pihole_guardian.config import Settings
from pihole_guardian.db import repository

FILTERS = ("all", "blocked", "allowed", "unknown", "needs_review", "recently_changed", "high_risk")


def domain_view_rows(
    conn: sqlite3.Connection, *, filter_name: str = "all", query: str | None = None, limit: int = 200
) -> list[dict]:
    settings_high_risk_threshold = 0.6  # matches the default monitor threshold; see docs for tuning via config

    if query:
        base = repository.search_domains(conn, query, limit=limit)
    else:
        base = repository.list_domains(conn, limit=limit)

    recently_changed_ids: set[int] = set()
    if filter_name == "recently_changed":
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        recently_changed_ids = {
            c["domain_id"] for c in conn.execute("SELECT domain_id FROM changes WHERE created_at >= ?", (cutoff,))
        }

    rows = []
    for domain in base:
        classification = repository.latest_classification(conn, domain.id)
        decision = repository.latest_decision(conn, domain.id)
        category = classification.category if classification else "unknown"
        confidence = classification.confidence if classification else 0.0

        if filter_name == "blocked" and domain.pihole_status != "denied":
            continue
        if filter_name == "allowed" and domain.pihole_status != "allowed":
            continue
        if filter_name == "unknown" and category != "unknown":
            continue
        if filter_name == "needs_review" and not (decision and decision.status == "pending"):
            continue
        if filter_name == "recently_changed" and domain.id not in recently_changed_ids:
            continue
        if filter_name == "high_risk" and not (
            category in {c.value for c in NEGATIVE_CATEGORIES} and confidence >= settings_high_risk_threshold
        ):
            continue

        rows.append(
            {
                "domain": domain.domain,
                "pihole_status": domain.pihole_status,
                "managed_by": domain.pihole_list_managed_by,
                "category": category,
                "confidence": confidence,
                "query_count": domain.query_count,
                "last_seen": domain.last_seen,
                "decision_action": decision.action if decision else None,
                "decision_status": decision.status if decision else None,
            }
        )
    return rows


def system_status(conn: sqlite3.Connection, settings: Settings, pihole_client) -> dict:
    total = conn.execute("SELECT COUNT(*) c FROM domains").fetchone()["c"]
    active = conn.execute("SELECT COUNT(*) c FROM domains WHERE active = 1").fetchone()["c"]
    auto_managed = conn.execute("SELECT COUNT(*) c FROM auto_managed_domains").fetchone()["c"]
    pending = conn.execute("SELECT COUNT(*) c FROM decisions WHERE status = 'pending'").fetchone()["c"]
    open_incidents = conn.execute("SELECT COUNT(*) c FROM incidents WHERE status = 'open'").fetchone()["c"]
    reachable = pihole_client.health_check()
    # Version detection (requirement #1) is surfaced here for visibility even
    # though this service doesn't yet branch its own behavior on it — the
    # PiholeClient abstraction is what would let a v5-specific implementation
    # be added later without this call site changing.
    version = pihole_client.get_version_info() if reachable else None
    return {
        "mode": settings.mode.value,
        "pihole_reachable": reachable,
        "pihole_core_version": version.core if version else None,
        "pihole_ftl_version": version.ftl if version else None,
        "domains_total": total,
        "domains_active": active,
        "auto_managed_count": auto_managed,
        "pending_decisions": pending,
        "open_incidents": open_incidents,
    }
