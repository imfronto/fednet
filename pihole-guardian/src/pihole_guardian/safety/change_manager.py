"""The change/rollback ledger (requirement #10) and the ONLY code path
allowed to mutate Pi-hole's domain lists.

Two invariants are enforced here, not just documented:

1. **Never touch a manually managed entry.** Before adding, this checks
   whether the domain is already present on the target list; if so, and it
   wasn't put there by this service (no comment tag it recognizes, or no
   matching `auto_managed_domains` row), the add/remove is refused
   (`UnsafeOperationRefused`) rather than silently overwriting or deleting
   someone's manual entry. This is checked on every call, not assumed from
   the database — Pi-hole's state is the ground truth being verified
   against, in case someone edited it directly.

2. **Every applied change is reversible.** `apply_block`/`apply_unblock`
   record a before/after snapshot under a change id before touching
   Pi-hole; `rollback` reads that snapshot back and restores it through the
   same guarded path, then marks the original change rolled back. A
   DRY_RUN/simulated change is recorded identically but never calls
   Pi-hole at all, so `service changes` shows exactly what *would* happen
   without any of the guard logic above needing a separate code path.
"""
from __future__ import annotations

import logging
import secrets
import sqlite3
from datetime import datetime, timezone

from pihole_guardian.db import repository
from pihole_guardian.db.models import Change, Domain
from pihole_guardian.logging_utils import log_event
from pihole_guardian.piholeclient.base import PiholeClient
from pihole_guardian.piholeclient.exceptions import PiholeError, UnsafeOperationRefused
from pihole_guardian.piholeclient.models import DomainListEntry

logger = logging.getLogger(__name__)

COMMENT_TAG_PREFIX = "pihole-guardian:"


def _make_comment_tag(classification: str) -> str:
    return f"{COMMENT_TAG_PREFIX}{classification}"


def _is_ours(entry: DomainListEntry | None) -> bool:
    return bool(entry and entry.comment and entry.comment.startswith(COMMENT_TAG_PREFIX))


def new_change_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{secrets.token_hex(4)}"


def apply_block(
    conn: sqlite3.Connection,
    pihole_client: PiholeClient,
    domain: Domain,
    decision_id: int | None,
    *,
    classification_category: str,
    confidence: float,
    evidence_summary: str,
    reason: str,
    actor: str,
    simulated: bool,
) -> Change:
    change_id = new_change_id()
    existing = pihole_client.find_domain_entry(domain.domain, "deny") if not simulated else None

    if simulated:
        previous_state = {"pihole_status": domain.pihole_status}
        new_state = {
            "pihole_status": "denied",
            "managed_by": "guardian",
            "classification": classification_category,
            "confidence": confidence,
            "evidence_summary": evidence_summary,
            "reason": reason,
        }
        change = repository.record_change(
            conn, change_id, domain.id, decision_id, "add_deny", previous_state, new_state, actor, simulated=True
        )
        log_event(
            logger, logging.INFO, "Simulated block (DRY_RUN)",
            domain=domain.domain, action="block", change_id=change_id, confidence=confidence,
        )
        return change

    if existing is not None and not _is_ours(existing):
        log_event(
            logger, logging.INFO, "Domain already denylisted manually; not claiming it as auto-managed",
            domain=domain.domain, change_id=change_id,
        )
        repository.set_pihole_status(conn, domain.id, "denied", "manual")
        previous_state = {"pihole_status": domain.pihole_status}
        new_state = {"pihole_status": "denied", "managed_by": "manual", "note": "pre-existing manual entry"}
        change = repository.record_change(
            conn, change_id, domain.id, decision_id, "add_deny", previous_state, new_state, actor
        )
        repository.mark_change_applied(conn, change_id)
        return change

    if existing is not None and _is_ours(existing):
        repository.upsert_auto_managed(
            conn, domain.id, classification_category, confidence, evidence_summary, reason, "guardian", None
        )
        repository.set_pihole_status(conn, domain.id, "denied", "guardian")
        previous_state = {"pihole_status": "denied", "managed_by": "guardian"}
        new_state = previous_state | {"note": "already blocked by this service; metadata refreshed"}
        change = repository.record_change(
            conn, change_id, domain.id, decision_id, "add_deny", previous_state, new_state, actor
        )
        repository.mark_change_applied(conn, change_id)
        return change

    previous_state = {"pihole_status": domain.pihole_status}
    new_state = {
        "pihole_status": "denied",
        "managed_by": "guardian",
        "classification": classification_category,
        "confidence": confidence,
        "evidence_summary": evidence_summary,
        "reason": reason,
    }
    change = repository.record_change(
        conn, change_id, domain.id, decision_id, "add_deny", previous_state, new_state, actor
    )
    try:
        pihole_client.add_domain(domain.domain, "deny", comment=_make_comment_tag(classification_category))
    except PiholeError as exc:
        repository.mark_change_applied(conn, change_id, error=str(exc))
        log_event(
            logger, logging.ERROR, "Failed to apply block",
            domain=domain.domain, action="block", change_id=change_id, error=str(exc),
        )
        raise

    repository.mark_change_applied(conn, change_id)
    repository.set_pihole_status(conn, domain.id, "denied", "guardian")
    repository.upsert_auto_managed(
        conn, domain.id, classification_category, confidence, evidence_summary, reason, "guardian", change_id
    )
    log_event(
        logger, logging.INFO, "Applied block",
        domain=domain.domain, action="block", change_id=change_id, classification=classification_category,
        confidence=confidence, previous_state=previous_state, new_state=new_state,
    )
    return repository.get_change(conn, change_id)  # type: ignore[return-value]


def apply_unblock(
    conn: sqlite3.Connection,
    pihole_client: PiholeClient,
    domain: Domain,
    decision_id: int | None,
    *,
    reason: str,
    actor: str,
    simulated: bool,
) -> Change:
    change_id = new_change_id()
    auto_managed = repository.get_auto_managed(conn, domain.id)

    if auto_managed is None:
        # We have no record of having added this block ourselves. Refuse to
        # remove it, even if the caller is the disruption detector — this is
        # exactly the "never touch a manually managed entry" boundary, and it
        # applies during incident response too (see safety/disruption.py).
        raise UnsafeOperationRefused(
            f"Refusing to unblock '{domain.domain}': no auto-managed record exists for it. "
            "It is either unblocked already or was blocked manually — manual entries are never "
            "modified automatically."
        )

    if simulated:
        previous_state = {
            "pihole_status": "denied",
            "managed_by": "guardian",
            "classification": auto_managed["classification"],
            "confidence": auto_managed["confidence"],
            "evidence_summary": auto_managed["evidence_summary"],
        }
        new_state = {"pihole_status": "unlisted"}
        change = repository.record_change(
            conn, change_id, domain.id, decision_id, "remove_deny", previous_state, new_state, actor, simulated=True
        )
        log_event(logger, logging.INFO, "Simulated unblock (DRY_RUN)", domain=domain.domain, change_id=change_id)
        return change

    existing = pihole_client.find_domain_entry(domain.domain, "deny")
    if existing is not None and not _is_ours(existing):
        raise UnsafeOperationRefused(
            f"Refusing to unblock '{domain.domain}': the live Pi-hole entry is not tagged as "
            "managed by this service (its comment may have been edited manually). Remove it by hand "
            "if intended."
        )

    previous_state = {
        "pihole_status": "denied",
        "managed_by": "guardian",
        "classification": auto_managed["classification"],
        "confidence": auto_managed["confidence"],
        "evidence_summary": auto_managed["evidence_summary"],
        "reason": auto_managed["reason"],
    }
    new_state = {"pihole_status": "unlisted"}
    change = repository.record_change(
        conn, change_id, domain.id, decision_id, "remove_deny", previous_state, new_state, actor
    )
    try:
        if existing is not None:
            pihole_client.remove_domain(domain.domain, "deny")
    except PiholeError as exc:
        repository.mark_change_applied(conn, change_id, error=str(exc))
        log_event(
            logger, logging.ERROR, "Failed to apply unblock",
            domain=domain.domain, action="unblock", change_id=change_id, error=str(exc),
        )
        raise

    repository.mark_change_applied(conn, change_id)
    repository.set_pihole_status(conn, domain.id, "unlisted", None)
    repository.remove_auto_managed(conn, domain.id)
    log_event(
        logger, logging.INFO, "Applied unblock",
        domain=domain.domain, action="unblock", change_id=change_id, reason=reason,
        previous_state=previous_state, new_state=new_state,
    )
    return repository.get_change(conn, change_id)  # type: ignore[return-value]


def rollback(conn: sqlite3.Connection, pihole_client: PiholeClient, change_id: str, actor: str) -> Change:
    change = repository.get_change(conn, change_id)
    if change is None:
        raise ValueError(f"No such change: {change_id}")
    if change.rolled_back:
        raise ValueError(f"Change {change_id} was already rolled back at {change.rolled_back_at}")
    if not change.applied or change.simulated:
        raise ValueError(f"Change {change_id} was never actually applied (simulated={change.simulated}); nothing to roll back")

    domain = repository.get_domain_by_id(conn, change.domain_id)
    if domain is None:
        raise ValueError(f"Domain {change.domain_id} referenced by change {change_id} no longer exists")

    target = change.previous_state
    inverse_change_id = new_change_id()

    if change.change_type == "add_deny":
        # Rolling back a block means removing it — but only if we're the ones
        # who added it (an add_deny change we recorded is definitionally ours,
        # except the "pre-existing manual entry" branch above, which is
        # correctly excluded from rollback below).
        if target.get("managed_by") == "manual":
            raise UnsafeOperationRefused(
                f"Change {change_id} recorded a pre-existing manual denylist entry, not one this "
                "service added — refusing to remove it via rollback."
            )
        existing = pihole_client.find_domain_entry(domain.domain, "deny")
        if existing is not None and not _is_ours(existing):
            raise UnsafeOperationRefused(
                f"Refusing to roll back {change_id}: the live Pi-hole entry for '{domain.domain}' is "
                "no longer tagged as managed by this service."
            )
        new_state = {"pihole_status": target.get("pihole_status", "unlisted")}
        inverse = repository.record_change(
            conn, inverse_change_id, domain.id, change.decision_id, "remove_deny", change.new_state, new_state,
            actor, simulated=False,
        )
        try:
            if existing is not None:
                pihole_client.remove_domain(domain.domain, "deny")
        except PiholeError as exc:
            repository.mark_change_applied(conn, inverse_change_id, error=str(exc))
            raise
        repository.mark_change_applied(conn, inverse_change_id)
        repository.set_pihole_status(conn, domain.id, new_state["pihole_status"], None)
        repository.remove_auto_managed(conn, domain.id)

    elif change.change_type == "remove_deny":
        # Rolling back an unblock means re-adding the block using the
        # snapshot taken right before it was removed.
        classification_category = target.get("classification", "unknown")
        confidence = target.get("confidence", 0.0)
        evidence_summary = target.get("evidence_summary", "")
        reason = target.get("reason", "restored via rollback of " + change_id)
        new_state = {
            "pihole_status": "denied",
            "managed_by": "guardian",
            "classification": classification_category,
            "confidence": confidence,
        }
        inverse = repository.record_change(
            conn, inverse_change_id, domain.id, change.decision_id, "add_deny", change.new_state, new_state,
            actor, simulated=False,
        )
        try:
            pihole_client.add_domain(domain.domain, "deny", comment=_make_comment_tag(classification_category))
        except PiholeError as exc:
            repository.mark_change_applied(conn, inverse_change_id, error=str(exc))
            raise
        repository.mark_change_applied(conn, inverse_change_id)
        repository.set_pihole_status(conn, domain.id, "denied", "guardian")
        repository.upsert_auto_managed(
            conn, domain.id, classification_category, confidence, evidence_summary, reason, "guardian",
            inverse_change_id,
        )
    else:
        raise ValueError(f"Rollback not implemented for change_type={change.change_type!r}")

    repository.mark_change_rolled_back(conn, change_id)
    log_event(
        logger, logging.INFO, "Rolled back change",
        domain=domain.domain, change_id=change_id, inverse_change_id=inverse_change_id, actor=actor,
    )
    return inverse  # type: ignore[return-value]
