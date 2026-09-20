"""VirusTotal domain reputation — optional, disabled unless an API key is
configured. Minimizes data sent: only the domain name itself, never any
query metadata, client info, or history (requirement #22's "minimize data
sent to external reputation providers").
"""
from __future__ import annotations

import logging

import httpx

from pihole_guardian.reputation.base import ReputationProvider, ReputationResult

logger = logging.getLogger(__name__)

_ENDPOINT_PREFIX = "https://www.virustotal.com/api/v3/domains/"
_TRACKING_CATEGORY_HINTS = ("tracking", "advertising", "ads")


class VirusTotalProvider(ReputationProvider):
    name = "virustotal"

    def __init__(self, *, api_key: str, timeout: float = 8.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def is_available(self) -> bool:
        return bool(self._api_key)

    def check(self, domain: str, registrable_domain: str) -> ReputationResult:
        if not self._api_key:
            return ReputationResult(self.name, "unknown", 0.0, detail="Provider not configured")

        try:
            resp = httpx.get(
                _ENDPOINT_PREFIX + registrable_domain,
                headers={"x-apikey": self._api_key},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            logger.info("VirusTotal lookup failed for %s: %s", registrable_domain, exc)
            return ReputationResult(self.name, "error", 0.0, detail=f"request failed: {exc}")

        if resp.status_code == 404:
            return ReputationResult(self.name, "unknown", 0.0, detail="No VirusTotal record")
        if resp.status_code != 200:
            return ReputationResult(self.name, "error", 0.0, detail=f"HTTP {resp.status_code}")

        try:
            attrs = resp.json()["data"]["attributes"]
            stats = attrs.get("last_analysis_stats", {})
            malicious = int(stats.get("malicious", 0))
            suspicious = int(stats.get("suspicious", 0))
            harmless = int(stats.get("harmless", 0))
            categories = " ".join(str(v).lower() for v in (attrs.get("categories") or {}).values())
        except (KeyError, TypeError, ValueError) as exc:
            return ReputationResult(self.name, "error", 0.0, detail=f"unparseable response: {exc}")

        if any(hint in categories for hint in _TRACKING_CATEGORY_HINTS):
            return ReputationResult(
                self.name, "tracking", weight=0.35, category="advertising_tracking",
                detail="VirusTotal vendor categories indicate tracking/advertising",
            )
        if malicious >= 3:
            return ReputationResult(
                self.name, "malicious", weight=min(0.5, 0.15 + 0.05 * malicious), category="threat_intel",
                detail=f"{malicious} security vendor(s) flag this domain as malicious",
            )
        if suspicious >= 3 and malicious == 0:
            return ReputationResult(
                self.name, "suspicious", weight=0.2, category="threat_intel",
                detail=f"{suspicious} security vendor(s) flag this domain as suspicious",
            )
        if harmless >= 30 and malicious == 0 and suspicious == 0:
            return ReputationResult(
                self.name, "legitimate", weight=0.15, category="threat_intel_clean",
                detail=f"{harmless} security vendors report this domain as harmless, none flag it",
            )
        return ReputationResult(self.name, "unknown", 0.0, detail="No clear consensus")
