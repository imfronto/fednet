"""Typed views over the SQLite rows used by more than one module. Kept
deliberately as plain dataclasses (not an ORM) — see db/migrator.py for why."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field


@dataclass
class Domain:
    id: int
    domain: str
    unicode_domain: str
    registrable_domain: str
    first_seen: str
    last_seen: str
    query_count: int
    distinct_client_count: int
    pihole_status: str
    pihole_list_managed_by: str | None
    active: bool
    created_at: str
    updated_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Domain":
        return Domain(
            id=row["id"],
            domain=row["domain"],
            unicode_domain=row["unicode_domain"],
            registrable_domain=row["registrable_domain"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            query_count=row["query_count"],
            distinct_client_count=row["distinct_client_count"],
            pihole_status=row["pihole_status"],
            pihole_list_managed_by=row["pihole_list_managed_by"],
            active=bool(row["active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class Evidence:
    id: int
    domain_id: int
    source: str
    category: str | None
    verdict: str
    weight: float
    detail: str | None
    observed_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Evidence":
        return Evidence(
            id=row["id"],
            domain_id=row["domain_id"],
            source=row["source"],
            category=row["category"],
            verdict=row["verdict"],
            weight=row["weight"],
            detail=row["detail"],
            observed_at=row["observed_at"],
        )


@dataclass
class Classification:
    id: int
    domain_id: int
    category: str
    confidence: float
    legitimate_signals: list[str]
    malicious_signals: list[str]
    conflict: bool
    reasoning: str
    created_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Classification":
        return Classification(
            id=row["id"],
            domain_id=row["domain_id"],
            category=row["category"],
            confidence=row["confidence"],
            legitimate_signals=json.loads(row["legitimate_signals"]),
            malicious_signals=json.loads(row["malicious_signals"]),
            conflict=bool(row["conflict"]),
            reasoning=row["reasoning"],
            created_at=row["created_at"],
        )


@dataclass
class Decision:
    id: int
    domain_id: int
    classification_id: int | None
    action: str
    confidence: float
    reason: str
    mode: str
    status: str
    created_at: str
    resolved_at: str | None
    resolved_by: str | None

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Decision":
        return Decision(
            id=row["id"],
            domain_id=row["domain_id"],
            classification_id=row["classification_id"],
            action=row["action"],
            confidence=row["confidence"],
            reason=row["reason"],
            mode=row["mode"],
            status=row["status"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
            resolved_by=row["resolved_by"],
        )


@dataclass
class Change:
    id: str
    domain_id: int
    decision_id: int | None
    change_type: str
    previous_state: dict = field(default_factory=dict)
    new_state: dict = field(default_factory=dict)
    simulated: bool = False
    applied: bool = False
    applied_at: str | None = None
    rolled_back: bool = False
    rolled_back_at: str | None = None
    rollback_of: str | None = None
    actor: str = "system"
    error: str | None = None
    created_at: str = ""

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Change":
        return Change(
            id=row["id"],
            domain_id=row["domain_id"],
            decision_id=row["decision_id"],
            change_type=row["change_type"],
            previous_state=json.loads(row["previous_state"]),
            new_state=json.loads(row["new_state"]),
            simulated=bool(row["simulated"]),
            applied=bool(row["applied"]),
            applied_at=row["applied_at"],
            rolled_back=bool(row["rolled_back"]),
            rolled_back_at=row["rolled_back_at"],
            rollback_of=row["rollback_of"],
            actor=row["actor"],
            error=row["error"],
            created_at=row["created_at"],
        )
