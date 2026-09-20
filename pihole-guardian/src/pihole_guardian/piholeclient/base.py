"""Pi-hole integration interface. Everything else in the codebase (collector,
safety/change_manager, CLI) talks to this abstraction, not to httpx or any
version-specific detail — so a Pi-hole v5 (PHP API) implementation, or a
completely different DNS blocker, can be dropped in later without touching
anything upstream of it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from pihole_guardian.piholeclient.models import DomainListEntry, PiholeVersionInfo, QueryRecord


class PiholeClient(ABC):
    @abstractmethod
    def health_check(self) -> bool:
        """Returns True if Pi-hole is reachable and authenticated. Must never
        raise for a routine connectivity failure — return False instead."""

    @abstractmethod
    def get_version_info(self) -> PiholeVersionInfo: ...

    @abstractmethod
    def get_recent_queries(
        self,
        *,
        since_unix: float | None = None,
        until_unix: float | None = None,
        domain: str | None = None,
        cursor: int | None = None,
        length: int = 500,
    ) -> tuple[list[QueryRecord], int | None]:
        """Returns (records, next_cursor). `next_cursor` is None when there is
        nothing more recent to page through."""

    @abstractmethod
    def find_domain_entry(self, domain: str, list_type: str) -> DomainListEntry | None:
        """list_type is 'allow' or 'deny'. Returns None if the domain is not
        present on that list (it may still be present on the other one)."""

    @abstractmethod
    def list_all_domain_entries(self) -> list[DomainListEntry]:
        """Every allow+deny entry currently on Pi-hole — used only for the
        periodic ground-truth sync (collector/list_collector.py), never for
        a per-decision check (find_domain_entry is used there instead, to
        avoid a full-list fetch on every classification)."""

    @abstractmethod
    def add_domain(
        self, domain: str, list_type: str, *, comment: str, kind: str = "exact", enabled: bool = True
    ) -> DomainListEntry: ...

    @abstractmethod
    def remove_domain(self, domain: str, list_type: str, *, kind: str = "exact") -> None: ...

    @abstractmethod
    def get_adlists(self) -> list[dict]:
        """Gravity's configured blocklist sources — used for reporting, not mutation."""

    @abstractmethod
    def trigger_gravity_update(self) -> Iterator[str]:
        """Streams the `pihole -g` output line by line as it runs."""

    @abstractmethod
    def close(self) -> None: ...
