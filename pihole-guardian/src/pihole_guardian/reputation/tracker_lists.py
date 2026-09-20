"""Bundled tracking/advertising list evidence source.

Deliberately NOT a live per-domain network lookup: the configured hosts-
format list URLs (default: StevenBlack's unified hosts list, the same
source many Pi-hole default adlists already use) are downloaded on a slow
refresh schedule (`list_refresh_interval_hours`) into an in-memory set, and
membership checks are then pure local set lookups. This keeps per-domain
classification fast, avoids leaking the live query stream to a third party,
and means a network blip during classification never affects this
provider's availability — only the periodic refresh can fail, and a failed
refresh simply keeps serving the last good snapshot.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

from pihole_guardian.reputation.base import ReputationProvider, ReputationResult

logger = logging.getLogger(__name__)


def _parse_hosts_format(text: str) -> set[str]:
    domains: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("0.0.0.0", "127.0.0.1", "::1"):
            domains.add(parts[1].lower())
        elif len(parts) == 1:
            domains.add(parts[0].lower())
    return domains


class TrackerListProvider(ReputationProvider):
    name = "tracker_list"

    def __init__(self, urls: list[str], *, refresh_interval_hours: int, http_timeout: float = 30.0) -> None:
        self._urls = urls
        self._refresh_interval = refresh_interval_hours * 3600
        self._timeout = http_timeout
        self._domains: set[str] = set()
        self._last_refresh: float = 0.0
        self._lock = threading.Lock()

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            if not force and (time.time() - self._last_refresh) < self._refresh_interval:
                return
            combined: set[str] = set()
            any_success = False
            for url in self._urls:
                try:
                    resp = httpx.get(url, timeout=self._timeout, follow_redirects=True)
                    resp.raise_for_status()
                    combined |= _parse_hosts_format(resp.text)
                    any_success = True
                except httpx.HTTPError as exc:
                    logger.warning(
                        "Tracker list refresh failed for %s: %s (keeping previous snapshot)", url, exc
                    )
            if any_success:
                self._domains = combined
                self._last_refresh = time.time()
                logger.info("Refreshed tracker list: %d domains", len(self._domains))

    def is_available(self) -> bool:
        return len(self._domains) > 0

    def check(self, domain: str, registrable_domain: str) -> ReputationResult:
        if domain in self._domains or registrable_domain in self._domains:
            return ReputationResult(
                source=self.name,
                verdict="tracking",
                weight=0.45,
                category="advertising_tracking",
                detail="Present in bundled tracking/advertising hosts list",
            )
        return ReputationResult(source=self.name, verdict="unknown", weight=0.0, detail="Not in tracker list")
