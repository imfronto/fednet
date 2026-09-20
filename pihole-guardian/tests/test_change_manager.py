import pytest

from pihole_guardian.db import repository
from pihole_guardian.domain_utils import normalize_domain
from pihole_guardian.piholeclient.exceptions import UnsafeOperationRefused
from pihole_guardian.safety.change_manager import apply_block, apply_unblock, rollback


def _domain(conn, name="evil.example.com"):
    return repository.upsert_domain_seen(conn, normalize_domain(name))


def test_apply_block_adds_to_denylist_and_records_auto_managed(conn, fake_pihole):
    domain = _domain(conn)
    change = apply_block(
        conn, fake_pihole, domain, None, classification_category="known_malicious", confidence=0.95,
        evidence_summary="two sources agree", reason="test block", actor="system", simulated=False,
    )
    assert change.applied
    assert domain.domain in fake_pihole._deny
    assert fake_pihole._deny[domain.domain].comment.startswith("pihole-guardian:")
    auto_managed = repository.get_auto_managed(conn, domain.id)
    assert auto_managed is not None
    refreshed = repository.get_domain(conn, domain.domain)
    assert refreshed.pihole_status == "denied"
    assert refreshed.pihole_list_managed_by == "guardian"


def test_dry_run_block_never_touches_pihole(conn, fake_pihole):
    domain = _domain(conn)
    change = apply_block(
        conn, fake_pihole, domain, None, classification_category="known_malicious", confidence=0.95,
        evidence_summary="x", reason="test", actor="system", simulated=True,
    )
    assert change.simulated
    assert domain.domain not in fake_pihole._deny
    assert repository.get_auto_managed(conn, domain.id) is None


def test_block_does_not_claim_preexisting_manual_entry(conn, fake_pihole):
    domain = _domain(conn)
    fake_pihole._deny[domain.domain] = fake_pihole.add_domain(domain.domain, "deny", comment="added by human directly")
    apply_block(
        conn, fake_pihole, domain, None, classification_category="known_malicious", confidence=0.95,
        evidence_summary="x", reason="test", actor="system", simulated=False,
    )
    assert repository.get_auto_managed(conn, domain.id) is None  # never claimed as ours
    refreshed = repository.get_domain(conn, domain.domain)
    assert refreshed.pihole_list_managed_by == "manual"


def test_unblock_refuses_domain_with_no_auto_managed_record(conn, fake_pihole):
    """This is the core 'never touch manually managed entries' guarantee:
    even if a domain is denylisted, if this service never recorded adding
    it, apply_unblock must refuse rather than remove someone else's entry."""
    domain = _domain(conn)
    fake_pihole.add_domain(domain.domain, "deny", comment="added by human directly")
    repository.set_pihole_status(conn, domain.id, "denied", "manual")

    with pytest.raises(UnsafeOperationRefused):
        apply_unblock(conn, fake_pihole, domain, None, reason="test", actor="system", simulated=False)

    assert domain.domain in fake_pihole._deny  # untouched


def test_unblock_removes_our_own_entry(conn, fake_pihole):
    domain = _domain(conn)
    apply_block(
        conn, fake_pihole, domain, None, classification_category="known_malicious", confidence=0.95,
        evidence_summary="x", reason="block", actor="system", simulated=False,
    )
    change = apply_unblock(conn, fake_pihole, domain, None, reason="revalidation cleared it", actor="system", simulated=False)
    assert change.applied
    assert domain.domain not in fake_pihole._deny
    assert repository.get_auto_managed(conn, domain.id) is None


def test_rollback_of_block_removes_it(conn, fake_pihole):
    domain = _domain(conn)
    change = apply_block(
        conn, fake_pihole, domain, None, classification_category="known_malicious", confidence=0.95,
        evidence_summary="x", reason="block", actor="system", simulated=False,
    )
    rollback(conn, fake_pihole, change.id, actor="user:test")
    assert domain.domain not in fake_pihole._deny
    assert repository.get_auto_managed(conn, domain.id) is None
    original = repository.get_change(conn, change.id)
    assert original.rolled_back


def test_rollback_of_unblock_restores_it(conn, fake_pihole):
    domain = _domain(conn)
    apply_block(
        conn, fake_pihole, domain, None, classification_category="known_malicious", confidence=0.95,
        evidence_summary="x", reason="block", actor="system", simulated=False,
    )
    unblock_change = apply_unblock(conn, fake_pihole, domain, None, reason="x", actor="system", simulated=False)
    rollback(conn, fake_pihole, unblock_change.id, actor="user:test")
    assert domain.domain in fake_pihole._deny
    assert repository.get_auto_managed(conn, domain.id) is not None


def test_cannot_rollback_the_same_change_twice(conn, fake_pihole):
    domain = _domain(conn)
    change = apply_block(
        conn, fake_pihole, domain, None, classification_category="known_malicious", confidence=0.95,
        evidence_summary="x", reason="block", actor="system", simulated=False,
    )
    rollback(conn, fake_pihole, change.id, actor="user:test")
    with pytest.raises(ValueError):
        rollback(conn, fake_pihole, change.id, actor="user:test")


def test_cannot_rollback_a_simulated_change(conn, fake_pihole):
    domain = _domain(conn)
    change = apply_block(
        conn, fake_pihole, domain, None, classification_category="known_malicious", confidence=0.95,
        evidence_summary="x", reason="block", actor="system", simulated=True,
    )
    with pytest.raises(ValueError):
        rollback(conn, fake_pihole, change.id, actor="user:test")
