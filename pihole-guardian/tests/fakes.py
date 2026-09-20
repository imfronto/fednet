"""In-memory fakes used across the test suite — no real network, no real
Pi-hole required (requirement #19: "do not require a real Pi-hole
installation to run the test suite"). `test_pihole_v6_client.py` separately
exercises the real HTTP client against a mocked transport to verify wire
compatibility; everything else uses this simpler fake so tests stay fast
and focused on the logic being tested rather than HTTP plumbing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pihole_guardian.piholeclient.base import PiholeClient
from pihole_guardian.piholeclient.exceptions import PiholeAPIError, PiholeConnectionError
from pihole_guardian.piholeclient.models import DomainListEntry, PiholeVersionInfo, QueryRecord
from pihole_guardian.reputation.base import ReputationProvider, ReputationResult


@dataclass
class FakePiholeClient(PiholeClient):
    unreachable: bool = False
    queued_queries: list[QueryRecord] = field(default_factory=list)
    _deny: dict[str, DomainListEntry] = field(default_factory=dict)
    _allow: dict[str, DomainListEntry] = field(default_factory=dict)
    gravity_runs: int = 0

    def _check_reachable(self):
        if self.unreachable:
            raise PiholeConnectionError("simulated outage")

    def health_check(self) -> bool:
        return not self.unreachable

    def get_version_info(self) -> PiholeVersionInfo:
        self._check_reachable()
        return PiholeVersionInfo(core="v6.0", ftl="v6.0", web="v6.0")

    def get_recent_queries(self, *, since_unix=None, until_unix=None, domain=None, cursor=None, length=500):
        self._check_reachable()
        records = self.queued_queries
        self.queued_queries = []
        return records, None

    def find_domain_entry(self, domain: str, list_type: str) -> DomainListEntry | None:
        self._check_reachable()
        table = self._deny if list_type == "deny" else self._allow
        return table.get(domain)

    def list_all_domain_entries(self) -> list[DomainListEntry]:
        self._check_reachable()
        return list(self._deny.values()) + list(self._allow.values())

    def add_domain(self, domain, list_type, *, comment, kind="exact", enabled=True) -> DomainListEntry:
        self._check_reachable()
        table = self._deny if list_type == "deny" else self._allow
        if domain in table:
            raise PiholeAPIError(400, "The item is already present", key="database_error")
        entry = DomainListEntry(domain=domain, list_type=list_type, kind=kind, comment=comment, enabled=enabled, id=len(table) + 1)
        table[domain] = entry
        return entry

    def remove_domain(self, domain, list_type, *, kind="exact") -> None:
        self._check_reachable()
        table = self._deny if list_type == "deny" else self._allow
        table.pop(domain, None)  # idempotent, like the real client

    def get_adlists(self) -> list[dict]:
        self._check_reachable()
        return []

    def trigger_gravity_update(self):
        self._check_reachable()
        self.gravity_runs += 1
        yield "gravity updated"

    def close(self) -> None:
        pass


class FixedResultProvider(ReputationProvider):
    """A reputation provider that always returns a predetermined result —
    used to construct exact evidence scenarios in classification/decision tests."""

    def __init__(self, name: str, result: ReputationResult, available: bool = True):
        self.name = name
        self._result = result
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def check(self, domain: str, registrable_domain: str) -> ReputationResult:
        return self._result


class RaisingProvider(ReputationProvider):
    name = "raising_provider"

    def check(self, domain: str, registrable_domain: str) -> ReputationResult:
        raise RuntimeError("simulated provider bug")


class SlowProvider(ReputationProvider):
    name = "slow_provider"

    def __init__(self, delay_seconds: float):
        self._delay = delay_seconds

    def check(self, domain: str, registrable_domain: str) -> ReputationResult:
        import time

        time.sleep(self._delay)
        return ReputationResult(self.name, "unknown", 0.0)
