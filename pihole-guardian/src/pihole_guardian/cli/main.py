"""CLI entry point. Installed as `pihole-guardian` (not `service` — that name
is already a system command on most Linux distributions; every command below
maps 1:1 to the `service <verb>` names in the project brief, e.g.
`service status` -> `pihole-guardian status`).

Every command opens its own short-lived context/connection and closes it
before exiting — this is a one-shot CLI process, not the long-running
service (see main.py for that).
"""
from __future__ import annotations

import json

import typer

from pihole_guardian import services
from pihole_guardian.classification.behavioral import lexical_hint
from pihole_guardian.classification.dependency import dependency_evidence
from pihole_guardian.classification.engine import classify
from pihole_guardian.collector.list_collector import sync_lists
from pihole_guardian.collector.query_collector import collect_once
from pihole_guardian.config import load_settings
from pihole_guardian.context import build_context
from pihole_guardian.db import repository
from pihole_guardian.logging_utils import configure_logging
from pihole_guardian.piholeclient.exceptions import PiholeError, UnsafeOperationRefused
from pihole_guardian.scheduler.jobs import decide_and_maybe_apply, revalidate_job

app = typer.Typer(help="Pi Guard — safety-first automated Pi-hole blocklist maintenance.", no_args_is_help=True)


def _echo_err(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)


@app.command()
def status():
    """Show operating mode, Pi-hole reachability, and key counts."""
    ctx = build_context()
    conn = ctx.connect()
    try:
        from pihole_guardian.api import views

        s = views.system_status(conn, ctx.settings, ctx.pihole_client)
        for k, v in s.items():
            typer.echo(f"{k}: {v}")
    finally:
        conn.close()
        ctx.close()


@app.command()
def health():
    """Exit 0 if Pi-hole and the local database are both reachable, else 1."""
    ctx = build_context()
    ok = True
    try:
        if not ctx.pihole_client.health_check():
            _echo_err("Pi-hole: UNREACHABLE")
            ok = False
        else:
            typer.echo("Pi-hole: reachable")
        conn = ctx.connect()
        conn.execute("SELECT 1")
        conn.close()
        typer.echo("Database: reachable")
    except Exception as exc:  # noqa: BLE001 — health check must report, not crash
        _echo_err(f"Database: UNREACHABLE ({exc})")
        ok = False
    finally:
        ctx.close()
    raise typer.Exit(0 if ok else 1)


@app.command()
def analyze(limit: int = typer.Option(200, help="Max domains to process this run.")):
    """Run one collect + classify + decide cycle immediately (does not wait for the scheduler)."""
    ctx = build_context()
    conn = ctx.connect()
    try:
        n = collect_once(conn, ctx.pihole_client, ctx.settings)
        typer.echo(f"Collected {n} query record(s)")
        sync_lists(conn, ctx.pihole_client)

        candidates = repository.domains_needing_classification(conn, 24 * 365, limit)
        classified = 0
        for domain in candidates:
            evidence = list(ctx.aggregator.gather(domain.domain, domain.registrable_domain))
            dep = dependency_evidence(conn, domain.registrable_domain, domain.id)
            if dep:
                evidence.append(dep)
            hint = lexical_hint(domain.domain)
            if hint:
                evidence.append(hint)
            result = classify(evidence, ctx.settings)
            for e in evidence:
                repository.add_evidence(conn, domain.id, e.source, e.verdict, e.weight, category=e.category, detail=e.detail)
            classification = repository.add_classification(
                conn, domain.id, result.category.value, result.confidence, result.legitimate_signals,
                result.malicious_signals, result.conflict, result.reasoning,
            )
            decide_and_maybe_apply(ctx, conn, domain, classification)
            classified += 1
        typer.echo(f"Classified and decided on {classified} domain(s)")
    finally:
        conn.close()
        ctx.close()


@app.command()
def review():
    """List decisions pending approval (RECOMMEND mode, or ENFORCE decisions below the auto-apply bar)."""
    ctx = build_context()
    conn = ctx.connect()
    try:
        pending = repository.pending_decisions(conn)
        if not pending:
            typer.echo("Nothing pending review.")
            return
        for d in pending:
            domain = repository.get_domain_by_id(conn, d.domain_id)
            typer.echo(f"[{d.id}] {domain.domain if domain else '?'} — {d.action} ({d.confidence:.0%}): {d.reason}")
        typer.echo("\nApprove with: pihole-guardian review-approve <id>   Reject with: pihole-guardian review-reject <id>")
    finally:
        conn.close()
        ctx.close()


@app.command("review-approve")
def review_approve(decision_id: int):
    ctx = build_context()
    conn = ctx.connect()
    try:
        change = services.approve_decision(conn, ctx.pihole_client, decision_id, actor="user:cli")
        typer.echo(f"Approved. Change {change.id} applied.")
    except (services.NotFoundError, services.InvalidOperationError) as exc:
        _echo_err(str(exc))
        raise typer.Exit(1)
    finally:
        conn.close()
        ctx.close()


@app.command("review-reject")
def review_reject(decision_id: int):
    ctx = build_context()
    conn = ctx.connect()
    try:
        services.reject_decision(conn, decision_id, actor="user:cli")
        typer.echo("Rejected.")
    except (services.NotFoundError, services.InvalidOperationError) as exc:
        _echo_err(str(exc))
        raise typer.Exit(1)
    finally:
        conn.close()
        ctx.close()


@app.command()
def explain(domain: str, json_output: bool = typer.Option(False, "--json")):
    """Show the full reasoning trail behind a domain's current status."""
    ctx = build_context()
    conn = ctx.connect()
    try:
        report = services.explain_domain(conn, domain)
    except services.NotFoundError as exc:
        _echo_err(str(exc))
        raise typer.Exit(1)
    except services.InvalidOperationError as exc:
        _echo_err(str(exc))
        raise typer.Exit(1)
    finally:
        conn.close()
        ctx.close()

    if json_output:
        from pihole_guardian.api.routes import serialize_explain

        typer.echo(json.dumps(serialize_explain(report), indent=2, default=str))
        return

    d = report.domain
    typer.echo(f"Domain: {d.domain}")
    typer.echo(f"Status: {d.pihole_status.upper()}" + (f"  (managed by: {d.pihole_list_managed_by})" if d.pihole_list_managed_by else ""))
    if report.is_protected:
        typer.echo(f"Protected: yes ({report.protected_reason or 'no reason recorded'})")
    typer.echo("")
    if report.classification:
        c = report.classification
        typer.echo(f"Classification: {c.category}")
        typer.echo(f"Confidence: {c.confidence:.0%}")
        if c.conflict:
            typer.echo("Conflict: evidence disagreed; confidence was reduced accordingly")
        typer.echo("\nEvidence:")
        for e in report.evidence:
            sign = "+" if e.verdict != "legitimate" else "-"
            typer.echo(f"  {sign} [{e.source}] {e.verdict}: {e.detail}")
        typer.echo("\nLegitimate indicators:")
        for s in c.legitimate_signals:
            typer.echo(f"  - {s}")
        if not c.legitimate_signals:
            typer.echo("  - none found")
    else:
        typer.echo("Classification: not yet classified")

    typer.echo("")
    if report.decision:
        typer.echo(f"Decision: {report.decision.action.upper()} (status: {report.decision.status})")
        typer.echo(f"Reason: {report.decision.reason}")
    if report.auto_managed:
        typer.echo(f"\nLast verified: {report.auto_managed['last_verified']}")


@app.command()
def changes(limit: int = 50):
    """List recent changes (add_deny / remove_deny / etc.) with their change IDs."""
    ctx = build_context()
    conn = ctx.connect()
    try:
        for c in repository.list_changes(conn, limit=limit):
            domain = repository.get_domain_by_id(conn, c.domain_id)
            flag = "SIMULATED" if c.simulated else ("ROLLED BACK" if c.rolled_back else ("APPLIED" if c.applied else "FAILED"))
            typer.echo(f"{c.id}  {domain.domain if domain else '?':40s}  {c.change_type:14s}  {flag:12s}  {c.actor}  {c.created_at}")
    finally:
        conn.close()
        ctx.close()


@app.command()
def rollback(change_id: str):
    """Reverse a previously applied change."""
    ctx = build_context()
    conn = ctx.connect()
    try:
        inverse = services.rollback(conn, ctx.pihole_client, change_id, actor="user:cli")
        typer.echo(f"Rolled back {change_id}. New change: {inverse.id}")
    except (ValueError, UnsafeOperationRefused, PiholeError) as exc:
        _echo_err(str(exc))
        raise typer.Exit(1)
    finally:
        conn.close()
        ctx.close()


@app.command()
def allow(domain: str, reason: str = typer.Option("", help="Why this domain is being allowed.")):
    """Protect a domain from automatic blocking, and unblock it now if it's currently auto-blocked."""
    ctx = build_context()
    conn = ctx.connect()
    try:
        result = services.manual_allow(conn, ctx.pihole_client, domain, reason=reason, actor="user:cli")
        typer.echo(f"'{domain}' added to the protected list.")
        if result["unblocked"]:
            typer.echo(f"Also removed the existing automatic block (change {result['change'].id}).")
    except services.InvalidOperationError as exc:
        _echo_err(str(exc))
        raise typer.Exit(1)
    finally:
        conn.close()
        ctx.close()


@app.command()
def unprotect(pattern: str):
    """Remove a pattern from the protected-domains list."""
    ctx = build_context()
    conn = ctx.connect()
    try:
        removed = services.unprotect_domain(conn, pattern)
        typer.echo("Removed." if removed else f"No protected-domain entry for pattern '{pattern}'.")
    finally:
        conn.close()
        ctx.close()


@app.command()
def block(domain: str, reason: str = typer.Option("", help="Why this domain is being blocked.")):
    """Immediately block a domain (bypasses the classification pipeline; still refuses protected domains)."""
    ctx = build_context()
    conn = ctx.connect()
    try:
        change = services.manual_block(conn, ctx.pihole_client, domain, reason=reason or "Manual CLI block", actor="user:cli")
        typer.echo(f"Blocked '{domain}'. Change: {change.id}")
    except services.InvalidOperationError as exc:
        _echo_err(str(exc))
        raise typer.Exit(1)
    finally:
        conn.close()
        ctx.close()


@app.command()
def verify(domain: str):
    """Run an on-demand reputation check for a domain right now."""
    ctx = build_context()
    conn = ctx.connect()
    try:
        classification = services.verify_domain(conn, ctx.aggregator, domain, ctx.settings)
        typer.echo(f"Category: {classification.category}")
        typer.echo(f"Confidence: {classification.confidence:.0%}")
        typer.echo(f"Reasoning: {classification.reasoning}")
    except services.InvalidOperationError as exc:
        _echo_err(str(exc))
        raise typer.Exit(1)
    finally:
        conn.close()
        ctx.close()


@app.command()
def revalidate():
    """Force an immediate revalidation pass over every auto-managed (auto-blocked) domain."""
    ctx = build_context()
    try:
        revalidate_job(ctx)
        typer.echo("Revalidation pass complete.")
    finally:
        ctx.close()


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_format)
    app()


if __name__ == "__main__":
    main()
