"""High-level use-case functions shared by the CLI and the API, so both
surfaces call exactly the same logic rather than duplicating it. Every
function here takes an already-open `sqlite3.Connection` and, where
relevant, a `PiholeClient` — callers (CLI commands, API route handlers) own
connection/context lifecycle.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pihole_guardian.classification.behavioral import lexical_hint
from pihole_guardian.classification.dependency import dependency_evidence
from pihole_guardian.classification.engine import classify
from pihole_guardian.db import repository
from pihole_guardian.db.models import Change, Classification, Decision, Domain
from pihole_guardian.domain_utils import normalize_domain
from pihole_guardian.piholeclient.base import PiholeClient
from pihole_guardian.reputation.aggregator import ReputationAggregator
from pihole_guardian.safety import protected_list
from pihole_guardian.safety.change_manager import apply_block, apply_unblock, rollback as rollback_change


class NotFoundError(Exception):
    pass


class InvalidOperationError(Exception):
    pass


@dataclass
class ExplainReport:
    domain: Domain
    classification: Classification | None
    decision: Decision | None
    auto_managed: dict | None
    is_protected: bool
    protected_reason: str | None
    evidence: list
    recent_changes: list[Change]
    open_incidents: list


def explain_domain(conn: sqlite3.Connection, domain_str: str) -> ExplainReport:
    normalized = normalize_domain(domain_str)
    if not normalized.valid:
        raise InvalidOperationError(f"'{domain_str}' is not a well-formed domain")
    domain = repository.get_domain(conn, normalized.domain)
    if domain is None:
        raise NotFoundError(f"No observations for '{normalized.domain}' yet")

    classification = repository.latest_classification(conn, domain.id)
    decision = repository.latest_decision(conn, domain.id)
    auto_managed_row = repository.get_auto_managed(conn, domain.id)
    auto_managed = dict(auto_managed_row) if auto_managed_row else None
    is_protected, protected_reason = protected_list.is_protected(conn, domain.domain)
    evidence = repository.latest_evidence(conn, domain.id, max_age_hours=24 * 30)
    changes = repository.list_changes(conn, domain_id=domain.id, limit=20)
    incidents = [
        row for row in repository.list_incidents(conn)
        if row["domain_id"] == domain.id and row["status"] == "open"
    ]

    return ExplainReport(
        domain=domain, classification=classification, decision=decision, auto_managed=auto_managed,
        is_protected=is_protected, protected_reason=protected_reason, evidence=evidence,
        recent_changes=changes, open_incidents=incidents,
    )


def verify_domain(
    conn: sqlite3.Connection, aggregator: ReputationAggregator, domain_str: str, settings
) -> Classification:
    """Runs an on-demand reputation check right now, independent of the
    scheduled classify_job — used by `service verify <domain>`."""
    normalized = normalize_domain(domain_str)
    if not normalized.valid:
        raise InvalidOperationError(f"'{domain_str}' is not a well-formed domain")
    domain = repository.upsert_domain_seen(conn, normalized, query_delta=0)

    evidence = list(aggregator.gather(domain.domain, domain.registrable_domain))
    dep = dependency_evidence(conn, domain.registrable_domain, domain.id)
    if dep:
        evidence.append(dep)
    hint = lexical_hint(domain.domain)
    if hint:
        evidence.append(hint)

    result = classify(evidence, settings)
    for e in evidence:
        repository.add_evidence(conn, domain.id, e.source, e.verdict, e.weight, category=e.category, detail=e.detail)
    return repository.add_classification(
        conn, domain.id, result.category.value, result.confidence, result.legitimate_signals,
        result.malicious_signals, result.conflict, result.reasoning,
    )


def manual_block(
    conn: sqlite3.Connection, pihole_client: PiholeClient, domain_str: str, *, reason: str, actor: str
) -> Change:
    normalized = normalize_domain(domain_str)
    if not normalized.valid:
        raise InvalidOperationError(f"'{domain_str}' is not a well-formed domain")
    is_protected, protected_reason = protected_list.is_protected(conn, normalized.domain)
    if is_protected:
        raise InvalidOperationError(
            f"'{normalized.domain}' is on the protected-domains list ({protected_reason or 'no reason recorded'}). "
            "Remove it from that list first (`service unprotect`) if you really intend to block it."
        )
    domain = repository.upsert_domain_seen(conn, normalized, query_delta=0)
    decision = repository.add_decision(
        conn, domain.id, None, "block", 1.0, f"Manually blocked by {actor}: {reason}", "manual", status="applied"
    )
    return apply_block(
        conn, pihole_client, domain, decision.id, classification_category="manual", confidence=1.0,
        evidence_summary=f"Manual action by {actor}", reason=reason, actor=actor, simulated=False,
    )


def manual_allow(
    conn: sqlite3.Connection, pihole_client: PiholeClient, domain_str: str, *, reason: str, actor: str
) -> dict:
    """Adds the domain to the protected list (so it is never auto-blocked
    again) and, if it is currently auto-blocked, removes that block."""
    normalized = normalize_domain(domain_str)
    if not normalized.valid:
        raise InvalidOperationError(f"'{domain_str}' is not a well-formed domain")
    repository.add_protected_domain(conn, normalized.domain, "exact", reason or f"Manually allowed by {actor}")

    domain = repository.upsert_domain_seen(conn, normalized, query_delta=0)
    result = {"protected": True, "unblocked": False, "change": None}
    if repository.get_auto_managed(conn, domain.id) is not None:
        decision = repository.add_decision(
            conn, domain.id, None, "unblock", 1.0, f"Manually allowed by {actor}: {reason}", "manual",
            status="applied",
        )
        change = apply_unblock(conn, pihole_client, domain, decision.id, reason=reason, actor=actor, simulated=False)
        result["unblocked"] = True
        result["change"] = change
    return result


def unprotect_domain(conn: sqlite3.Connection, pattern: str) -> bool:
    return repository.remove_protected_domain(conn, pattern)


def approve_decision(conn: sqlite3.Connection, pihole_client: PiholeClient, decision_id: int, actor: str) -> Change:
    decision = repository.get_decision(conn, decision_id)
    if decision is None:
        raise NotFoundError(f"No such decision: {decision_id}")
    if decision.status != "pending":
        raise InvalidOperationError(f"Decision {decision_id} is not pending (status={decision.status})")
    domain = repository.get_domain_by_id(conn, decision.domain_id)
    if domain is None:
        raise NotFoundError(f"Domain for decision {decision_id} no longer exists")
    try:
        if decision.action == "block":
            change = apply_block(
                conn, pihole_client, domain, decision.id,
                classification_category=_classification_category(conn, decision.classification_id),
                confidence=decision.confidence, evidence_summary=decision.reason, reason=decision.reason,
                actor=actor, simulated=False,
            )
        elif decision.action == "unblock":
            change = apply_unblock(conn, pihole_client, domain, decision.id, reason=decision.reason, actor=actor, simulated=False)
        else:
            raise InvalidOperationError(f"Decision action '{decision.action}' is not an approvable mutation")
    except Exception:
        repository.resolve_decision(conn, decision_id, "error", actor)
        raise
    repository.resolve_decision(conn, decision_id, "approved", actor)
    return change


def _classification_category(conn: sqlite3.Connection, classification_id: int | None) -> str:
    if classification_id is None:
        return "unknown"
    row = conn.execute("SELECT category FROM classifications WHERE id = ?", (classification_id,)).fetchone()
    return row["category"] if row else "unknown"


def reject_decision(conn: sqlite3.Connection, decision_id: int, actor: str) -> None:
    decision = repository.get_decision(conn, decision_id)
    if decision is None:
        raise NotFoundError(f"No such decision: {decision_id}")
    if decision.status != "pending":
        raise InvalidOperationError(f"Decision {decision_id} is not pending (status={decision.status})")
    repository.resolve_decision(conn, decision_id, "rejected", actor)


def rollback(conn: sqlite3.Connection, pihole_client: PiholeClient, change_id: str, actor: str) -> Change:
    return rollback_change(conn, pihole_client, change_id, actor)
