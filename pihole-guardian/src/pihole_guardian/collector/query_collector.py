"""Pulls recent DNS queries from Pi-hole and folds them into the per-domain
aggregates (`domains`, `observations`, `recent_buckets`, optionally
`client_seen`). Never stores raw per-query rows — see requirement #15
("do not store unnecessary personal information about DNS clients") and the
architecture note in db/migrations/0001_initial.sql about the two-tier
observation model.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import sqlite3
from datetime import datetime, timezone

from pihole_guardian.config import Settings
from pihole_guardian.db import repository
from pihole_guardian.domain_utils import normalize_domain
from pihole_guardian.piholeclient.base import PiholeClient
from pihole_guardian.piholeclient.exceptions import PiholeError
from pihole_guardian.piholeclient.models import QueryRecord

logger = logging.getLogger(__name__)

_CONFIG_KEY_LAST_SEEN = "collector.last_query_time_seen"
_MAX_PAGES_PER_CYCLE = 5
_PAGE_LENGTH = 1000


def _bucket_start(unix_time: float, minutes: int) -> str:
    dt = datetime.fromtimestamp(unix_time, tz=timezone.utc)
    floored_minute = (dt.minute // minutes) * minutes
    bucket = dt.replace(minute=floored_minute, second=0, microsecond=0)
    return bucket.isoformat()


def _client_hash(client_ip: str, salt: str) -> str:
    return hmac.new(salt.encode(), client_ip.encode(), hashlib.sha256).hexdigest()[:32]


def collect_once(conn: sqlite3.Connection, pihole_client: PiholeClient, settings: Settings) -> int:
    """Returns the number of query records processed. Never raises for a
    Pi-hole connectivity failure — logs and returns 0, so the scheduler loop
    keeps running and simply tries again next tick (requirement #17)."""
    last_seen_str = repository.get_config(conn, _CONFIG_KEY_LAST_SEEN)
    since = float(last_seen_str) if last_seen_str else None

    total_processed = 0
    max_time_seen = since or 0.0
    page_from = since
    touched_domains: dict[int, set[str]] = {}

    try:
        for _ in range(_MAX_PAGES_PER_CYCLE):
            records, _cursor = pihole_client.get_recent_queries(since_unix=page_from, length=_PAGE_LENGTH)
            if not records:
                break
            for record in records:
                _process_record(conn, record, settings, touched_domains)
                max_time_seen = max(max_time_seen, record.time)
                total_processed += 1
            if len(records) < _PAGE_LENGTH:
                break
            # Advance strictly past the latest timestamp seen this page to
            # avoid re-fetching the same records forever if Pi-hole always
            # returns >= _PAGE_LENGTH matches for a given `from`.
            page_from = max_time_seen + 0.000001
    except PiholeError as exc:
        logger.warning("Query collection failed (Pi-hole unreachable or rejected the request): %s", exc)
        # Still persist any progress made across earlier pages this cycle —
        # falling back to `since` on the next tick would otherwise
        # re-process (and double-count) everything already handled above.
        if total_processed and max_time_seen > (since or 0.0):
            repository.set_config(conn, _CONFIG_KEY_LAST_SEEN, repr(max_time_seen))
        return total_processed

    if settings.client_tracking_enabled:
        for domain_id in touched_domains:
            repository.set_distinct_client_count(conn, domain_id, repository.distinct_client_count(conn, domain_id))

    if total_processed and max_time_seen > (since or 0.0):
        repository.set_config(conn, _CONFIG_KEY_LAST_SEEN, repr(max_time_seen))
    return total_processed


def _process_record(
    conn: sqlite3.Connection, record: QueryRecord, settings: Settings, touched_domains: dict[int, set[str]]
) -> None:
    normalized = normalize_domain(record.domain)
    if not normalized.valid:
        logger.debug("Skipping unparseable domain from query log: %r", record.domain)
        return

    domain_row = repository.upsert_domain_seen(conn, normalized)
    observed_date = datetime.fromtimestamp(record.time, tz=timezone.utc).strftime("%Y-%m-%d")
    repository.record_daily_observation(
        conn,
        domain_row.id,
        observed_date,
        query_delta=1,
        nxdomain_delta=1 if record.is_nxdomain else 0,
        servfail_delta=1 if record.is_servfail else 0,
        blocked_delta=1 if record.was_blocked_by_pihole else 0,
        ok_delta=1 if record.resolved_ok else 0,
        record_types={record.qtype} if record.qtype else None,
    )
    bucket = _bucket_start(record.time, settings.recent_bucket_minutes)
    repository.bump_recent_bucket(
        conn,
        domain_row.id,
        bucket,
        ok=1 if record.resolved_ok else 0,
        nxdomain=1 if record.is_nxdomain else 0,
        servfail=1 if record.is_servfail else 0,
        blocked=1 if record.was_blocked_by_pihole else 0,
    )

    if settings.client_tracking_enabled and record.client_ip and settings.client_hash_salt:
        client_hash = _client_hash(record.client_ip, settings.client_hash_salt)
        repository.record_client_seen(conn, domain_row.id, client_hash)
        touched_domains.setdefault(domain_row.id, set()).add(client_hash)
