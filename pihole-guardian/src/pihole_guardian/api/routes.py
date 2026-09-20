from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from pihole_guardian import services
from pihole_guardian.api import views
from pihole_guardian.api.auth import require_api_token
from pihole_guardian.context import AppContext
from pihole_guardian.db import repository


def build_api_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    auth = require_api_token(ctx.settings)

    @router.get("/health")
    def health():
        return {"status": "ok"}

    @router.get("/status", dependencies=[Depends(auth)])
    def status():
        conn = ctx.connect()
        try:
            return views.system_status(conn, ctx.settings, ctx.pihole_client)
        finally:
            conn.close()

    @router.get("/domains", dependencies=[Depends(auth)])
    def list_domains(filter: str = "all", q: str | None = None, limit: int = 200):
        if filter not in views.FILTERS:
            raise HTTPException(400, f"Unknown filter '{filter}'. Valid: {', '.join(views.FILTERS)}")
        conn = ctx.connect()
        try:
            return views.domain_view_rows(conn, filter_name=filter, query=q, limit=limit)
        finally:
            conn.close()

    @router.get("/domains/{domain}/explain", dependencies=[Depends(auth)])
    def explain(domain: str):
        conn = ctx.connect()
        try:
            report = services.explain_domain(conn, domain)
            return serialize_explain(report)
        except services.NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except services.InvalidOperationError as exc:
            raise HTTPException(400, str(exc))
        finally:
            conn.close()

    @router.post("/domains/{domain}/verify", dependencies=[Depends(auth)])
    def verify(domain: str):
        conn = ctx.connect()
        try:
            classification = services.verify_domain(conn, ctx.aggregator, domain, ctx.settings)
            return {
                "domain": domain,
                "category": classification.category,
                "confidence": classification.confidence,
                "reasoning": classification.reasoning,
            }
        except services.InvalidOperationError as exc:
            raise HTTPException(400, str(exc))
        finally:
            conn.close()

    @router.post("/domains/{domain}/block", dependencies=[Depends(auth)])
    def block(domain: str, request: Request, reason: str = Body("", embed=True)):
        conn = ctx.connect()
        try:
            actor = f"user:{getattr(request.state, 'actor', 'api')}"
            change = services.manual_block(conn, ctx.pihole_client, domain, reason=reason or "Manual block via API", actor=actor)
            return _serialize_change(change)
        except services.InvalidOperationError as exc:
            raise HTTPException(400, str(exc))
        finally:
            conn.close()

    @router.post("/domains/{domain}/allow", dependencies=[Depends(auth)])
    def allow(domain: str, request: Request, reason: str = Body("", embed=True)):
        conn = ctx.connect()
        try:
            actor = f"user:{getattr(request.state, 'actor', 'api')}"
            result = services.manual_allow(conn, ctx.pihole_client, domain, reason=reason or "Manual allow via API", actor=actor)
            return {
                "protected": result["protected"],
                "unblocked": result["unblocked"],
                "change": _serialize_change(result["change"]) if result["change"] else None,
            }
        except services.InvalidOperationError as exc:
            raise HTTPException(400, str(exc))
        finally:
            conn.close()

    @router.get("/protected", dependencies=[Depends(auth)])
    def list_protected():
        conn = ctx.connect()
        try:
            return [dict(row) for row in repository.list_protected_domains(conn)]
        finally:
            conn.close()

    @router.delete("/protected/{pattern}", dependencies=[Depends(auth)])
    def unprotect(pattern: str):
        conn = ctx.connect()
        try:
            removed = services.unprotect_domain(conn, pattern)
            if not removed:
                raise HTTPException(404, f"No protected-domain entry for pattern '{pattern}'")
            return {"removed": True}
        finally:
            conn.close()

    @router.get("/changes", dependencies=[Depends(auth)])
    def list_changes(limit: int = 100):
        conn = ctx.connect()
        try:
            return [_serialize_change(c) for c in repository.list_changes(conn, limit=limit)]
        finally:
            conn.close()

    @router.post("/changes/{change_id}/rollback", dependencies=[Depends(auth)])
    def rollback(change_id: str, request: Request):
        conn = ctx.connect()
        try:
            actor = f"user:{getattr(request.state, 'actor', 'api')}"
            change = services.rollback(conn, ctx.pihole_client, change_id, actor)
            return _serialize_change(change)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        finally:
            conn.close()

    @router.get("/review", dependencies=[Depends(auth)])
    def review_queue():
        conn = ctx.connect()
        try:
            return [_serialize_decision(d) for d in repository.pending_decisions(conn)]
        finally:
            conn.close()

    @router.post("/review/{decision_id}/approve", dependencies=[Depends(auth)])
    def approve(decision_id: int, request: Request):
        conn = ctx.connect()
        try:
            actor = f"user:{getattr(request.state, 'actor', 'api')}"
            change = services.approve_decision(conn, ctx.pihole_client, decision_id, actor)
            return _serialize_change(change)
        except services.NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except services.InvalidOperationError as exc:
            raise HTTPException(400, str(exc))
        finally:
            conn.close()

    @router.post("/review/{decision_id}/reject", dependencies=[Depends(auth)])
    def reject(decision_id: int, request: Request):
        conn = ctx.connect()
        try:
            actor = f"user:{getattr(request.state, 'actor', 'api')}"
            services.reject_decision(conn, decision_id, actor)
            return {"rejected": True}
        except services.NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except services.InvalidOperationError as exc:
            raise HTTPException(400, str(exc))
        finally:
            conn.close()

    @router.get("/incidents", dependencies=[Depends(auth)])
    def list_incidents(status: str | None = None):
        conn = ctx.connect()
        try:
            return [dict(row) for row in repository.list_incidents(conn, status=status)]
        finally:
            conn.close()

    @router.post("/incidents/{incident_id}/resolve", dependencies=[Depends(auth)])
    def resolve_incident(incident_id: int):
        conn = ctx.connect()
        try:
            repository.resolve_incident(conn, incident_id, "reviewed")
            return {"resolved": True}
        finally:
            conn.close()

    return router


def _serialize_change(change) -> dict:
    if change is None:
        return None
    return {
        "id": change.id,
        "domain_id": change.domain_id,
        "change_type": change.change_type,
        "previous_state": change.previous_state,
        "new_state": change.new_state,
        "simulated": change.simulated,
        "applied": change.applied,
        "applied_at": change.applied_at,
        "rolled_back": change.rolled_back,
        "actor": change.actor,
        "error": change.error,
        "created_at": change.created_at,
    }


def _serialize_decision(decision) -> dict:
    return {
        "id": decision.id,
        "domain_id": decision.domain_id,
        "action": decision.action,
        "confidence": decision.confidence,
        "reason": decision.reason,
        "mode": decision.mode,
        "status": decision.status,
        "created_at": decision.created_at,
    }


def serialize_explain(report) -> dict:
    return {
        "domain": report.domain.domain,
        "pihole_status": report.domain.pihole_status,
        "managed_by": report.domain.pihole_list_managed_by,
        "query_count": report.domain.query_count,
        "first_seen": report.domain.first_seen,
        "last_seen": report.domain.last_seen,
        "is_protected": report.is_protected,
        "protected_reason": report.protected_reason,
        "classification": (
            {
                "category": report.classification.category,
                "confidence": report.classification.confidence,
                "reasoning": report.classification.reasoning,
                "legitimate_signals": report.classification.legitimate_signals,
                "malicious_signals": report.classification.malicious_signals,
                "conflict": report.classification.conflict,
            }
            if report.classification
            else None
        ),
        "decision": _serialize_decision(report.decision) if report.decision else None,
        "auto_managed": report.auto_managed,
        "evidence": [
            {
                "source": e.source, "verdict": e.verdict, "weight": e.weight, "category": e.category,
                "detail": e.detail, "observed_at": e.observed_at,
            }
            for e in report.evidence
        ],
        "recent_changes": [_serialize_change(c) for c in report.recent_changes],
        "open_incidents": [dict(i) for i in report.open_incidents],
    }
