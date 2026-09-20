from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from pihole_guardian import services
from pihole_guardian.api import views
from pihole_guardian.api.auth import require_dashboard_auth
from pihole_guardian.context import AppContext
from pihole_guardian.db import repository
from pihole_guardian.domain_utils import normalize_domain

_TEMPLATES_DIR = Path(__file__).parent / "templates"
logger = logging.getLogger(__name__)


def build_dashboard_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    auth = require_dashboard_auth(ctx.settings)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    def _render(request: Request, template_name: str, active_tab: str, **extra):
        # Starlette's TemplateResponse takes `request` as its own positional
        # argument (verified against the actually-installed version — the
        # older `TemplateResponse(name, {"request": request, ...})` two-arg
        # form silently misroutes every positional argument one slot to the
        # left instead of raising, which is what makes this worth a comment).
        context = {"active_tab": active_tab, "mode": ctx.settings.mode.value, **extra}
        return templates.TemplateResponse(request, template_name, context)

    @router.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, filter: str = "all", q: str | None = None, user=Depends(auth)):
        conn = ctx.connect()
        try:
            rows = views.domain_view_rows(conn, filter_name=filter, query=q)
            status = views.system_status(conn, ctx.settings, ctx.pihole_client)
            return _render(
                request, "dashboard.html", "dashboard",
                rows=rows, status=status, filters=views.FILTERS, filter_name=filter, query=q,
            )
        finally:
            conn.close()

    @router.get("/domains/{domain}", response_class=HTMLResponse)
    def domain_detail(domain: str, request: Request, user=Depends(auth)):
        conn = ctx.connect()
        try:
            report = services.explain_domain(conn, domain)
            return _render(request, "domain_detail.html", "dashboard", report=report)
        except services.NotFoundError:
            return HTMLResponse(f"No observations for '{domain}' yet.", status_code=404)
        finally:
            conn.close()

    @router.post("/domains/{domain}/block")
    def block(domain: str, request: Request, reason: str = Form(""), user=Depends(auth)):
        conn = ctx.connect()
        try:
            services.manual_block(conn, ctx.pihole_client, domain, reason=reason or "Manual block via dashboard", actor=f"user:{user}")
        except services.InvalidOperationError:
            pass
        finally:
            conn.close()
        return RedirectResponse(f"/domains/{domain}", status_code=303)

    @router.post("/domains/{domain}/allow")
    def allow(domain: str, request: Request, reason: str = Form(""), user=Depends(auth)):
        conn = ctx.connect()
        try:
            services.manual_allow(conn, ctx.pihole_client, domain, reason=reason or "Manual allow via dashboard", actor=f"user:{user}")
        except services.InvalidOperationError:
            pass
        finally:
            conn.close()
        return RedirectResponse(f"/domains/{domain}", status_code=303)

    @router.post("/domains/{domain}/verify")
    def verify(domain: str, request: Request, user=Depends(auth)):
        conn = ctx.connect()
        try:
            services.verify_domain(conn, ctx.aggregator, domain, ctx.settings)
        except services.InvalidOperationError:
            pass
        finally:
            conn.close()
        return RedirectResponse(f"/domains/{domain}", status_code=303)

    @router.get("/review", response_class=HTMLResponse)
    def review(request: Request, user=Depends(auth)):
        conn = ctx.connect()
        try:
            decisions = []
            for d in repository.pending_decisions(conn):
                dom = repository.get_domain_by_id(conn, d.domain_id)
                decisions.append({"id": d.id, "domain": dom.domain if dom else "?", "action": d.action,
                                   "confidence": d.confidence, "reason": d.reason})
            return _render(request, "review.html", "review", decisions=decisions)
        finally:
            conn.close()

    @router.post("/review/{decision_id}/approve")
    def approve(decision_id: int, user=Depends(auth)):
        conn = ctx.connect()
        try:
            services.approve_decision(conn, ctx.pihole_client, decision_id, actor=f"user:{user}")
        except (services.NotFoundError, services.InvalidOperationError):
            pass
        finally:
            conn.close()
        return RedirectResponse("/review", status_code=303)

    @router.post("/review/{decision_id}/reject")
    def reject(decision_id: int, user=Depends(auth)):
        conn = ctx.connect()
        try:
            services.reject_decision(conn, decision_id, actor=f"user:{user}")
        except (services.NotFoundError, services.InvalidOperationError):
            pass
        finally:
            conn.close()
        return RedirectResponse("/review", status_code=303)

    @router.get("/changes", response_class=HTMLResponse)
    def changes(request: Request, user=Depends(auth)):
        conn = ctx.connect()
        try:
            rows = []
            for c in repository.list_changes(conn, limit=200):
                dom = repository.get_domain_by_id(conn, c.domain_id)
                rows.append({**c.__dict__, "domain": dom.domain if dom else "?"})
            return _render(request, "changes.html", "changes", changes=rows)
        finally:
            conn.close()

    @router.post("/changes/{change_id}/rollback")
    def rollback(change_id: str, user=Depends(auth)):
        conn = ctx.connect()
        try:
            services.rollback(conn, ctx.pihole_client, change_id, actor=f"user:{user}")
        except Exception:
            logger.exception("Dashboard rollback of %s failed", change_id)
        finally:
            conn.close()
        return RedirectResponse("/changes", status_code=303)

    @router.get("/incidents", response_class=HTMLResponse)
    def incidents(request: Request, user=Depends(auth)):
        conn = ctx.connect()
        try:
            rows = [dict(i) for i in repository.list_incidents(conn)]
            return _render(request, "incidents.html", "incidents", incidents=rows)
        finally:
            conn.close()

    @router.post("/incidents/{incident_id}/resolve")
    def resolve_incident(incident_id: int, user=Depends(auth)):
        conn = ctx.connect()
        try:
            repository.resolve_incident(conn, incident_id, "reviewed")
        finally:
            conn.close()
        return RedirectResponse("/incidents", status_code=303)

    @router.get("/protected", response_class=HTMLResponse)
    def protected(request: Request, user=Depends(auth)):
        conn = ctx.connect()
        try:
            rows = [dict(p) for p in repository.list_protected_domains(conn)]
            return _render(request, "protected.html", "protected", protected_domains=rows)
        finally:
            conn.close()

    @router.post("/protected/add")
    def add_protected(pattern: str = Form(...), match_type: str = Form("exact"), reason: str = Form(""), user=Depends(auth)):
        conn = ctx.connect()
        try:
            normalized = normalize_domain(pattern.lstrip("*."))
            canonical = ("*." + normalized.domain) if match_type == "wildcard" else normalized.domain
            repository.add_protected_domain(conn, canonical, match_type, reason or f"Added via dashboard by {user}")
        finally:
            conn.close()
        return RedirectResponse("/protected", status_code=303)

    @router.post("/protected/{pattern}/remove")
    def remove_protected(pattern: str, user=Depends(auth)):
        conn = ctx.connect()
        try:
            services.unprotect_domain(conn, pattern)
        finally:
            conn.close()
        return RedirectResponse("/protected", status_code=303)

    return router
