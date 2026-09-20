"""Same-registrable-domain dependency evidence (requirement #12).

Scope note (stated explicitly rather than silently implied): this is
deliberately limited to "shares a registrable domain with a domain that is
already protected or known-legitimate" — e.g. `api.example.com` inherits a
weak protective signal because `example.com` is protected. It is NOT a full
behavioral co-occurrence graph across arbitrary domains queried close
together in time (which would require correlating client-level request
timing — more invasive of privacy and much more prone to false
correlations). Building that is a reasonable future enhancement but is out
of scope for this version; see docs/ARCHITECTURE.md.
"""
from __future__ import annotations

import sqlite3

from pihole_guardian.db import repository
from pihole_guardian.reputation.base import ReputationResult


def dependency_evidence(
    conn: sqlite3.Connection, registrable_domain: str, domain_id: int
) -> ReputationResult | None:
    siblings = repository.domains_sharing_registrable(conn, registrable_domain, domain_id)
    for sibling in siblings:
        if sibling.pihole_status == "allowed":
            return ReputationResult(
                source="dependency_same_registrable_domain",
                verdict="legitimate",
                weight=0.15,
                category="shares_registrable_domain_with_allowed",
                detail=f"Shares registrable domain '{registrable_domain}' with {sibling.domain}, "
                "which is explicitly allowed",
            )
        classification = repository.latest_classification(conn, sibling.id)
        if classification and classification.category in ("legitimate_infrastructure", "known_legitimate"):
            return ReputationResult(
                source="dependency_same_registrable_domain",
                verdict="legitimate",
                weight=0.15,
                category="shares_registrable_domain_with_legitimate",
                detail=f"Shares registrable domain '{registrable_domain}' with {sibling.domain}, "
                f"classified as {classification.category}",
            )
    return None
