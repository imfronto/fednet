from datetime import datetime, timedelta, timezone

from pihole_guardian.db import repository
from pihole_guardian.domain_utils import normalize_domain
from pihole_guardian.safety.disruption import evaluate_disruption


def _domain(conn, name):
    return repository.upsert_domain_seen(conn, normalize_domain(name))


def _bump(conn, domain_id, when: datetime, **kwargs):
    repository.bump_recent_bucket(conn, domain_id, when.isoformat(), **kwargs)


def test_no_signal_with_insufficient_post_block_data(conn, settings):
    domain = _domain(conn, "blocked.example.com")
    now = datetime.now(timezone.utc)
    block_at = (now - timedelta(minutes=10)).isoformat()
    signal = evaluate_disruption(conn, domain, block_at, settings)
    assert signal is None


def test_no_signal_when_rate_is_stable(conn, settings):
    domain = _domain(conn, "stable.example.com")
    now = datetime.now(timezone.utc)
    block_at = now - timedelta(hours=1)

    # Steady baseline before the block...
    for i in range(10):
        _bump(conn, domain.id, block_at - timedelta(minutes=5 * i), ok=1)
    # ...and a comparably steady rate after it (no retry storm).
    for i in range(10):
        _bump(conn, domain.id, block_at + timedelta(minutes=5 * i), blocked=1)

    signal = evaluate_disruption(conn, domain, block_at.isoformat(), settings)
    assert signal is None


def test_signal_fires_on_query_rate_spike(conn, settings):
    domain = _domain(conn, "spiking.example.com")
    now = datetime.now(timezone.utc)
    block_at = now - timedelta(minutes=30)

    # Light baseline: a couple of queries in the lookback window.
    _bump(conn, domain.id, block_at - timedelta(minutes=10), ok=2)
    # Retry-storm after the block: far more queries than the baseline rate.
    for i in range(30):
        _bump(conn, domain.id, block_at + timedelta(minutes=i), blocked=5)

    signal = evaluate_disruption(conn, domain, block_at.isoformat(), settings)
    assert signal is not None
    assert signal.rate_ratio >= settings.disruption_query_rate_spike_multiplier or signal.rate_ratio == -1.0
    assert "note" in signal.evidence
    assert "cannot confirm" in signal.evidence["note"]


def test_signal_fires_on_sibling_failure_rate(conn, settings):
    blocked = _domain(conn, "blocked.example.com")
    sibling = _domain(conn, "api.example.com")
    now = datetime.now(timezone.utc)
    block_at = now - timedelta(minutes=30)

    for i in range(10):
        _bump(conn, blocked.id, block_at + timedelta(minutes=i), blocked=1)
    for i in range(10):
        _bump(conn, sibling.id, block_at + timedelta(minutes=i), nxdomain=1)

    signal = evaluate_disruption(conn, blocked, block_at.isoformat(), settings)
    assert signal is not None
    assert any(f["domain"] == "api.example.com" for f in signal.sibling_failures)


def test_blocked_sibling_failures_are_not_counted(conn, settings):
    """A sibling that is ALSO blocked would tautologically show failures —
    those must not count as disruption evidence."""
    blocked = _domain(conn, "blocked.example.com")
    also_blocked_sibling = _domain(conn, "also-blocked.example.com")
    repository.set_pihole_status(conn, also_blocked_sibling.id, "denied", "guardian")
    now = datetime.now(timezone.utc)
    block_at = now - timedelta(minutes=30)

    for i in range(10):
        _bump(conn, blocked.id, block_at + timedelta(minutes=i), blocked=1)
    for i in range(10):
        _bump(conn, also_blocked_sibling.id, block_at + timedelta(minutes=i), nxdomain=1)

    signal = evaluate_disruption(conn, blocked, block_at.isoformat(), settings)
    if signal:
        assert not any(f["domain"] == "also-blocked.example.com" for f in signal.sibling_failures)
