"""Service-disruption detection (requirement #11).

Honesty about what DNS-only visibility can and can't show, stated once
here rather than left implicit: once a domain is blocked, Pi-hole answers
its queries itself (as NXDOMAIN or a null IP, depending on blocking mode)
*by design* — so "the blocked domain now shows NXDOMAIN" is tautological,
not evidence of anything. The two signals below were chosen specifically
because they are NOT tautological:

  1. Query-rate spike on the newly blocked domain itself, relative to its
     own pre-block baseline. A well-behaved client that fails once and
     moves on doesn't do this; a client stuck retrying a now-broken
     dependency does. This is a classic retry-storm proxy.
  2. NXDOMAIN/SERVFAIL rate on *sibling* domains (sharing the blocked
     domain's registrable domain — the same relation
     classification/dependency.py uses as evidence) that are NOT themselves
     blocked — if those start failing shortly after the block, something
     the blocked domain fed into may have broken.

Both are correlational, not causal, and this module never claims otherwise:
it produces an `open` incident for a human (or the automatic-unblock path
in ENFORCE, see below) to look at, tagged as "possible disruption" — never
a confirmed outage.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pihole_guardian.config import Settings
from pihole_guardian.db import repository
from pihole_guardian.db.models import Domain

logger = logging.getLogger(__name__)


@dataclass
class DisruptionSignal:
    domain_id: int
    domain: str
    post_query_total: int
    pre_query_total: int
    rate_ratio: float
    sibling_failures: list[dict]
    evidence: dict


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def evaluate_disruption(
    conn: sqlite3.Connection, domain: Domain, block_applied_at_iso: str, settings: Settings
) -> DisruptionSignal | None:
    now = datetime.now(timezone.utc)
    block_at = _parse(block_applied_at_iso)
    if block_at.tzinfo is None:
        block_at = block_at.replace(tzinfo=timezone.utc)

    post_hours = max((now - block_at).total_seconds() / 3600, 0.01)
    post = repository.recent_bucket_totals(conn, domain.id, since_iso=block_applied_at_iso)
    post_total = sum(post.values())
    if post_total < settings.disruption_min_queries:
        return None

    lookback = timedelta(hours=settings.disruption_lookback_hours)
    pre_since = (block_at - lookback).isoformat()
    pre = repository.recent_bucket_totals(conn, domain.id, since_iso=pre_since, until_iso=block_applied_at_iso)
    pre_total = sum(pre.values())
    pre_hours = settings.disruption_lookback_hours

    post_rate = post_total / post_hours
    pre_rate = pre_total / max(pre_hours, 0.01)
    # A domain with no pre-block baseline at all can't have a meaningful
    # ratio computed; treat cautiously rather than dividing by zero into an
    # artificially huge ratio.
    rate_ratio = (post_rate / pre_rate) if pre_rate > 0 else (float("inf") if post_rate > 0 else 0.0)

    sibling_failures = []
    siblings = repository.domains_sharing_registrable(conn, domain.registrable_domain, domain.id)
    for sibling in siblings:
        if sibling.pihole_status == "denied":
            continue  # a blocked sibling's own failure counts are tautological too
        sibling_post = repository.recent_bucket_totals(conn, sibling.id, since_iso=block_applied_at_iso)
        sibling_total = sum(sibling_post.values())
        if sibling_total < settings.disruption_min_queries:
            continue
        failure_rate = (sibling_post["nxdomain"] + sibling_post["servfail"]) / sibling_total
        if failure_rate >= settings.disruption_sibling_failure_rate_threshold:
            sibling_failures.append(
                {"domain": sibling.domain, "failure_rate": failure_rate, "queries": sibling_total}
            )

    is_signal = rate_ratio >= settings.disruption_query_rate_spike_multiplier or bool(sibling_failures)
    if not is_signal:
        return None

    evidence = {
        "post_query_total": post_total,
        "pre_query_total": pre_total,
        "post_rate_per_hour": round(post_rate, 2),
        "pre_rate_per_hour": round(pre_rate, 2),
        "rate_ratio": round(rate_ratio, 2) if rate_ratio != float("inf") else "inf",
        "sibling_failures": sibling_failures,
        "note": "Correlational only — DNS telemetry cannot confirm an application outage. "
        "This is a possible-disruption signal for human review, not a confirmed incident.",
    }
    return DisruptionSignal(
        domain_id=domain.id,
        domain=domain.domain,
        post_query_total=post_total,
        pre_query_total=pre_total,
        rate_ratio=rate_ratio if rate_ratio != float("inf") else -1.0,
        sibling_failures=sibling_failures,
        evidence=evidence,
    )
