"""Requirement #17: the service must tolerate Pi-hole being offline, and
never interpret 'unable to verify' as malicious."""
import pytest

from pihole_guardian.collector.list_collector import sync_lists
from pihole_guardian.collector.query_collector import collect_once
from pihole_guardian.db import repository
from pihole_guardian.domain_utils import normalize_domain
from pihole_guardian.piholeclient.exceptions import UnsafeOperationRefused
from pihole_guardian.safety.change_manager import apply_unblock
from tests.fakes import FakePiholeClient


def test_collect_once_survives_pihole_outage(conn, settings):
    pihole = FakePiholeClient(unreachable=True)
    n = collect_once(conn, pihole, settings)
    assert n == 0  # degrades gracefully, does not raise


def test_sync_lists_survives_pihole_outage(conn):
    pihole = FakePiholeClient(unreachable=True)
    sync_lists(conn, pihole)  # must not raise


def test_health_check_reports_false_without_raising():
    pihole = FakePiholeClient(unreachable=True)
    assert pihole.health_check() is False


def test_unblock_refuses_rather_than_guess_when_pihole_unreachable(conn):
    domain = repository.upsert_domain_seen(conn, normalize_domain("example.com"))
    pihole = FakePiholeClient(unreachable=True)
    # No auto_managed record exists — this must be refused regardless of
    # reachability, not silently treated as "nothing to unblock".
    with pytest.raises(UnsafeOperationRefused):
        apply_unblock(conn, pihole, domain, None, reason="test", actor="system", simulated=False)
