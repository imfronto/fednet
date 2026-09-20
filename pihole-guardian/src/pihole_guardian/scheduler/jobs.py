"""The periodic jobs the scheduler runs. Each function opens its own short
DB connection and is written to never raise out of itself for an expected
failure mode (Pi-hole down, a provider down, a malformed record) — an
unexpected exception is still allowed to propagate so the scheduler's own
top-level handler logs it loudly rather than a job silently going quiet.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pihole_guardian.classification.behavioral import lexical_hint
from pihole_guardian.classification.categories import Category
from pihole_guardian.classification.dependency import dependency_evidence
from pihole_guardian.classification.engine import ClassificationResult, classify
from pihole_guardian.collector.list_collector import sync_lists
from pihole_guardian.collector.query_collector import collect_once
from pihole_guardian.config import Mode
from pihole_guardian.context import AppContext
from pihole_guardian.db import repository
from pihole_guardian.decision.engine import decide, gate_for_mode
from pihole_guardian.logging_utils import log_event
from pihole_guardian.piholeclient.exceptions import PiholeError, UnsafeOperationRefused
from pihole_guardian.safety import protected_list
from pihole_guardian.safety.change_manager import apply_block, apply_unblock
from pihole_guardian.safety.disruption import evaluate_disruption

logger = logging.getLogger(__name__)

_CLASSIFY_BATCH_SIZE = 100
_CLASSIFY_MAX_AGE_HOURS = 12


def collect_job(ctx: AppContext) -> None:
    conn = ctx.connect()
    try:
        n = collect_once(conn, ctx.pihole_client, ctx.settings)
        if n:
            logger.info("Collected %d query records", n)
        sync_lists(conn, ctx.pihole_client)
    finally:
        conn.close()


def classify_job(ctx: AppContext) -> None:
    conn = ctx.connect()
    try:
        candidates = repository.domains_needing_classification(conn, _CLASSIFY_MAX_AGE_HOURS, _CLASSIFY_BATCH_SIZE)
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
                repository.add_evidence(
                    conn, domain.id, e.source, e.verdict, e.weight, category=e.category, detail=e.detail
                )
            classification = repository.add_classification(
                conn,
                domain.id,
                result.category.value,
                result.confidence,
                result.legitimate_signals,
                result.malicious_signals,
                result.conflict,
                result.reasoning,
            )
            log_event(
                logger, logging.INFO, "Classified domain",
                domain=domain.domain, classification=classification.category, confidence=classification.confidence,
            )
    finally:
        conn.close()


def decide_job(ctx: AppContext) -> None:
    conn = ctx.connect()
    try:
        candidates = repository.list_domains(conn, active_only=True, limit=_CLASSIFY_BATCH_SIZE)
        for domain in candidates:
            classification = repository.latest_classification(conn, domain.id)
            if classification is None:
                continue
            existing_decision = repository.latest_decision(conn, domain.id)
            if existing_decision is not None and existing_decision.classification_id == classification.id:
                continue  # already decided against this exact classification

            decide_and_maybe_apply(ctx, conn, domain, classification)
    finally:
        conn.close()


def decide_and_maybe_apply(ctx, conn, domain, classification) -> None:
    is_protected, protected_reason = protected_list.is_protected(conn, domain.domain)
    currently_auto_blocked = repository.get_auto_managed(conn, domain.id) is not None

    # An open disruption incident on this domain is a standing "do not
    # re-block without human review" gate (requirement #11's "do not
    # immediately re-block it") — short-circuit straight to flag_for_review.
    if not currently_auto_blocked and repository.has_open_incident(conn, domain.id) and classification.category in (
        Category.KNOWN_MALICIOUS.value, Category.STRONGLY_SUSPICIOUS.value, Category.ADVERTISING_TRACKING.value,
        Category.TELEMETRY_ANALYTICS.value, Category.POTENTIALLY_UNWANTED.value,
    ):
        repository.add_decision(
            conn, domain.id, classification.id, "flag_for_review", classification.confidence,
            "An open service-disruption incident exists for this domain from a previous automatic "
            "block; it will not be auto-blocked again until a human resolves the incident.",
            ctx.settings.mode.value, status="observed",
        )
        log_event(logger, logging.INFO, "Decision", domain=domain.domain, action="flag_for_review",
                   classification=classification.category, confidence=classification.confidence)
        return

    result = ClassificationResult(
        category=Category(classification.category),
        confidence=classification.confidence,
        hard_legitimate_match=classification.category == Category.LEGITIMATE_INFRASTRUCTURE.value,
        legitimate_signals=classification.legitimate_signals,
        malicious_signals=classification.malicious_signals,
        conflict=classification.conflict,
        independent_negative_sources=len({s.split(":")[0] for s in classification.malicious_signals}),
        reasoning=classification.reasoning,
    )
    outcome = decide(
        result, is_protected=is_protected, protected_reason=protected_reason,
        currently_auto_blocked=currently_auto_blocked, settings=ctx.settings,
    )
    status, should_apply = gate_for_mode(outcome.action, outcome.confidence, ctx.settings.mode, ctx.settings)

    decision = repository.add_decision(
        conn, domain.id, classification.id, outcome.action, outcome.confidence, outcome.reason,
        ctx.settings.mode.value, status=status,
    )
    log_event(
        logger, logging.INFO, "Decision", domain=domain.domain, action=outcome.action,
        classification=classification.category, confidence=outcome.confidence, mode=ctx.settings.mode.value,
        status=status,
    )

    if status == "simulated":
        _apply(ctx, conn, domain, decision, classification, outcome, actor="system", simulated=True)
        repository.resolve_decision(conn, decision.id, "simulated", "system")
    elif status == "auto":
        try:
            _apply(ctx, conn, domain, decision, classification, outcome, actor="system", simulated=False)
            repository.resolve_decision(conn, decision.id, "applied", "system")
        except (PiholeError, UnsafeOperationRefused) as exc:
            repository.resolve_decision(conn, decision.id, "error", "system")
            log_event(logger, logging.ERROR, "Failed to auto-apply decision", domain=domain.domain,
                       action=outcome.action, error=str(exc))


def _apply(ctx, conn, domain, decision, classification, outcome, *, actor: str, simulated: bool) -> None:
    if outcome.action == "block":
        apply_block(
            conn, ctx.pihole_client, domain, decision.id,
            classification_category=classification.category, confidence=outcome.confidence,
            evidence_summary=classification.reasoning, reason=outcome.reason, actor=actor, simulated=simulated,
        )
    elif outcome.action == "unblock":
        apply_unblock(conn, ctx.pihole_client, domain, decision.id, reason=outcome.reason, actor=actor, simulated=simulated)


def revalidate_job(ctx: AppContext) -> None:
    conn = ctx.connect()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ctx.settings.revalidation_interval_hours)
        for row in repository.list_auto_managed(conn):
            last_verified = datetime.fromisoformat(row["last_verified"])
            if last_verified.tzinfo is None:
                last_verified = last_verified.replace(tzinfo=timezone.utc)
            if last_verified > cutoff:
                continue
            domain = repository.get_domain_by_id(conn, row["domain_id"])
            if domain is None:
                continue

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
            log_event(logger, logging.INFO, "Revalidated domain", domain=domain.domain,
                       classification=classification.category, confidence=classification.confidence)
            decide_and_maybe_apply(ctx, conn, domain, classification)
    finally:
        conn.close()


def disruption_check_job(ctx: AppContext) -> None:
    conn = ctx.connect()
    try:
        for row in repository.list_auto_managed(conn):
            change = repository.get_change(conn, row["source_change_id"]) if row["source_change_id"] else None
            if change is None or not change.applied_at:
                continue
            domain = repository.get_domain_by_id(conn, row["domain_id"])
            if domain is None:
                continue
            signal = evaluate_disruption(conn, domain, change.applied_at, ctx.settings)
            if signal is None:
                continue

            log_event(logger, logging.WARNING, "Possible disruption signal detected", domain=domain.domain,
                       evidence=signal.evidence)
            try:
                # Bypasses the normal mode gate deliberately: this only ever
                # touches a domain this service already blocked with
                # authorization (automatic in ENFORCE, or human-approved),
                # and requirement #22 mandates preferring a false negative
                # over causing an outage — leaving a self-inflicted possible
                # disruption in place because the operator later lowered the
                # mode would violate that, not honor it.
                apply_unblock(
                    conn, ctx.pihole_client, domain, None,
                    reason="Temporarily removed due to a possible service-disruption signal; see the "
                    "linked incident for details. This is correlational evidence, not confirmed proof "
                    "of an outage.",
                    actor="system:disruption_detector", simulated=False,
                )
                incident_id = repository.create_incident(conn, domain.id, change.id, signal.evidence, True)
            except UnsafeOperationRefused as exc:
                incident_id = repository.create_incident(conn, domain.id, change.id, signal.evidence, False)
                log_event(logger, logging.WARNING, "Could not auto-unblock during disruption response",
                           domain=domain.domain, error=str(exc), incident_id=incident_id)
    finally:
        conn.close()


def build_scheduled_jobs(settings) -> list:
    from pihole_guardian.scheduler.runner import ScheduledJob

    return [
        ScheduledJob("collect", settings.poll_interval_seconds, collect_job),
        ScheduledJob("classify", settings.classify_interval_seconds, classify_job),
        ScheduledJob("decide", settings.decide_interval_seconds, decide_job),
        ScheduledJob("revalidate", settings.revalidation_interval_hours * 3600, revalidate_job),
        ScheduledJob("disruption_check", settings.disruption_check_interval_seconds, disruption_check_job),
        ScheduledJob("cleanup", 3600, cleanup_job),
    ]


def cleanup_job(ctx: AppContext) -> None:
    conn = ctx.connect()
    try:
        repository.purge_old_observations(conn, ctx.settings.retention_days_observations)
        repository.purge_old_recent_buckets(conn, ctx.settings.retention_hours_recent_buckets)
        repository.purge_old_changes(conn, ctx.settings.retention_days_changes)
        repository.mark_inactive_domains(conn, older_than_days=ctx.settings.retention_days_observations)
    finally:
        conn.close()
