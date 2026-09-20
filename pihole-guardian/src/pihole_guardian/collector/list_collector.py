"""Syncs `domains.pihole_status` / `pihole_list_managed_by` against Pi-hole's
actual allow/deny lists — the ground truth. This is what lets the dashboard
and CLI show accurate state even when a domain was added/removed directly
in Pi-hole's own admin UI, outside this service entirely, and it's how a
domain that was manually un-denylisted gets its stale 'denied' status
cleared here rather than only at decision time.
"""
from __future__ import annotations

import logging
import sqlite3

from pihole_guardian.db import repository
from pihole_guardian.domain_utils import normalize_domain
from pihole_guardian.piholeclient.base import PiholeClient
from pihole_guardian.piholeclient.exceptions import PiholeError
from pihole_guardian.safety.change_manager import COMMENT_TAG_PREFIX

logger = logging.getLogger(__name__)


def sync_lists(conn: sqlite3.Connection, pihole_client: PiholeClient) -> None:
    try:
        entries = pihole_client.list_all_domain_entries()
    except PiholeError as exc:
        logger.warning("Could not sync Pi-hole domain lists: %s", exc)
        return

    previously_listed_ids = {d.id for d in repository.list_domains(conn, status="denied")} | {
        d.id for d in repository.list_domains(conn, status="allowed")
    }
    touched_ids: set[int] = set()

    for entry in entries:
        normalized = normalize_domain(entry.domain)
        if not normalized.valid:
            continue
        domain_row = repository.upsert_domain_seen(conn, normalized, query_delta=0)
        touched_ids.add(domain_row.id)
        managed_by = "guardian" if (entry.comment or "").startswith(COMMENT_TAG_PREFIX) else "manual"
        status = "denied" if entry.list_type == "deny" else "allowed"
        repository.set_pihole_status(conn, domain_row.id, status, managed_by)

    for stale_id in previously_listed_ids - touched_ids:
        repository.set_pihole_status(conn, stale_id, "unlisted", None)
        domain = repository.get_domain_by_id(conn, stale_id)
        if domain and repository.get_auto_managed(conn, stale_id):
            logger.info(
                "Domain '%s' was removed from Pi-hole's lists outside this service; clearing our "
                "auto-managed record for it",
                domain.domain,
            )
            repository.remove_auto_managed(conn, stale_id)
