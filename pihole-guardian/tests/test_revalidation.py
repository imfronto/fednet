from datetime import datetime, timedelta, timezone

from pihole_guardian.config import Mode
from pihole_guardian.context import AppContext
from pihole_guardian.db import repository
from pihole_guardian.domain_utils import normalize_domain
from pihole_guardian.reputation.aggregator import ReputationAggregator
from pihole_guardian.reputation.base import ReputationResult
from pihole_guardian.safety.change_manager import apply_block
from pihole_guardian.scheduler.jobs import revalidate_job
from tests.fakes import FixedResultProvider


def _make_ctx(settings, conn, fake_pihole, provider_result, mode: Mode) -> AppContext:
    provider = FixedResultProvider("threat_intel", provider_result)
    aggregator = ReputationAggregator([provider], timeout_seconds=2.0)
    settings = settings.model_copy(update={"mode": mode})
    return AppContext(settings=settings, pihole_client=fake_pihole, aggregator=aggregator)


def _make_stale_auto_blocked_domain(conn, fake_pihole):
    domain = repository.upsert_domain_seen(conn, normalize_domain("formerly-bad.example.com"))
    apply_block(
        conn, fake_pihole, domain, None, classification_category="known_malicious", confidence=0.95,
        evidence_summary="two sources agreed", reason="initial block", actor="system", simulated=False,
    )
    # Force the auto-managed record to look overdue for revalidation.
    stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    conn.execute("UPDATE auto_managed_domains SET last_verified = ? WHERE domain_id = ?", (stale, domain.id))
    return domain


def test_revalidation_unblocks_in_enforce_mode_when_evidence_clears(settings, conn, fake_pihole):
    domain = _make_stale_auto_blocked_domain(conn, fake_pihole)
    ctx = _make_ctx(
        settings, conn, fake_pihole,
        ReputationResult("threat_intel", "unknown", 0.0, detail="no longer flagged"),
        Mode.ENFORCE,
    )
    revalidate_job(ctx)

    assert domain.domain not in fake_pihole._deny
    assert repository.get_auto_managed(conn, domain.id) is None
    refreshed = repository.get_domain(conn, domain.domain)
    assert refreshed.pihole_status == "unlisted"


def test_revalidation_in_recommend_mode_only_queues_for_approval(settings, conn, fake_pihole):
    domain = _make_stale_auto_blocked_domain(conn, fake_pihole)
    ctx = _make_ctx(
        settings, conn, fake_pihole,
        ReputationResult("threat_intel", "unknown", 0.0, detail="no longer flagged"),
        Mode.RECOMMEND,
    )
    revalidate_job(ctx)

    # Still blocked — RECOMMEND mode never mutates automatically, even to unblock.
    assert domain.domain in fake_pihole._deny
    assert repository.get_auto_managed(conn, domain.id) is not None
    pending = repository.pending_decisions(conn)
    assert any(d.domain_id == domain.id and d.action == "unblock" for d in pending)


def test_revalidation_keeps_block_when_evidence_still_supports_it(settings, conn, fake_pihole):
    domain = _make_stale_auto_blocked_domain(conn, fake_pihole)
    # Second independent source needed to re-clear the block threshold —
    # simulate with a strong provider that still flags it.
    ctx = _make_ctx(
        settings, conn, fake_pihole,
        ReputationResult("threat_intel", "malicious", 0.5, detail="still flagged"),
        Mode.ENFORCE,
    )
    revalidate_job(ctx)

    assert domain.domain in fake_pihole._deny
    assert repository.get_auto_managed(conn, domain.id) is not None
