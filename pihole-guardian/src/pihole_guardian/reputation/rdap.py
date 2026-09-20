"""RDAP domain-registration lookup, via the public rdap.org bootstrap
service (no API key). Provides only a weak, advisory signal — domain age —
never a block-eligible verdict by itself.

Per requirement #4 ("do not automatically block merely because... newly
registered") and #22 ("do not pretend DNS telemetry can determine intent
with certainty"), this provider deliberately caps its own weight far below
what a single source could use to push a decision toward BLOCK on its own,
and only ever labels a domain 'suspicious' (never 'malicious') based on age.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from pihole_guardian.reputation.base import ReputationProvider, ReputationResult

logger = logging.getLogger(__name__)

_ENDPOINT_PREFIX = "https://rdap.org/domain/"
_NEW_DOMAIN_DAYS = 7
_ESTABLISHED_DOMAIN_DAYS = 365


class RDAPProvider(ReputationProvider):
    name = "rdap"

    def __init__(self, *, timeout: float = 8.0) -> None:
        self._timeout = timeout

    def check(self, domain: str, registrable_domain: str) -> ReputationResult:
        url = _ENDPOINT_PREFIX + registrable_domain
        try:
            resp = httpx.get(url, timeout=self._timeout, follow_redirects=True)
        except httpx.HTTPError as exc:
            logger.info("RDAP lookup failed for %s: %s", registrable_domain, exc)
            return ReputationResult(self.name, "error", 0.0, detail=f"request failed: {exc}")

        if resp.status_code == 404:
            return ReputationResult(self.name, "unknown", 0.0, detail="No RDAP record (unregistered or private TLD)")
        if resp.status_code != 200:
            return ReputationResult(self.name, "error", 0.0, detail=f"HTTP {resp.status_code}")

        try:
            body = resp.json()
            events = body.get("events", [])
            registration = next((e for e in events if e.get("eventAction") == "registration"), None)
            if not registration:
                return ReputationResult(self.name, "unknown", 0.0, detail="No registration event in RDAP record")
            registered_at = datetime.fromisoformat(registration["eventDate"].replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError) as exc:
            return ReputationResult(self.name, "error", 0.0, detail=f"unparseable RDAP response: {exc}")

        age_days = (datetime.now(timezone.utc) - registered_at).days
        if age_days < _NEW_DOMAIN_DAYS:
            return ReputationResult(
                self.name,
                "suspicious",
                weight=0.15,  # intentionally low — age alone must never drive a block
                category="newly_registered",
                detail=f"Registered {age_days} day(s) ago (weak signal only)",
            )
        if age_days > _ESTABLISHED_DOMAIN_DAYS:
            return ReputationResult(
                self.name,
                "legitimate",
                weight=0.1,  # intentionally low — a weak corroborating signal only
                category="established_registration",
                detail=f"Registered {age_days} days ago",
            )
        return ReputationResult(self.name, "unknown", 0.0, detail=f"Registered {age_days} days ago (unremarkable)")
