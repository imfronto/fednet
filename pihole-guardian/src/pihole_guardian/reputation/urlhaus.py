"""abuse.ch URLhaus host lookup — malware distribution/C2 host reputation.

abuse.ch has moved several of its feeds behind a free "Auth-Key" registration
over time; this client supports sending one if configured
(`urlhaus_api_key`) but does not require it, since the exact current auth
requirement can't be verified from inside this environment. Any
unauthenticated/unexpected response (401, unrecognized shape, timeout) is
treated as verdict='error', never 'malicious' — see base.py's contract.
"""
from __future__ import annotations

import logging

import httpx

from pihole_guardian.reputation.base import ReputationProvider, ReputationResult

logger = logging.getLogger(__name__)

_ENDPOINT = "https://urlhaus-api.abuse.ch/v1/host/"


class URLhausProvider(ReputationProvider):
    name = "urlhaus"

    def __init__(self, *, api_key: str = "", timeout: float = 8.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def check(self, domain: str, registrable_domain: str) -> ReputationResult:
        headers = {"Auth-Key": self._api_key} if self._api_key else {}
        try:
            resp = httpx.post(_ENDPOINT, data={"host": domain}, headers=headers, timeout=self._timeout)
        except httpx.HTTPError as exc:
            logger.info("URLhaus lookup failed for %s: %s", domain, exc)
            return ReputationResult(self.name, "error", 0.0, detail=f"request failed: {exc}")

        if resp.status_code != 200:
            return ReputationResult(self.name, "error", 0.0, detail=f"HTTP {resp.status_code}")

        try:
            body = resp.json()
        except ValueError:
            return ReputationResult(self.name, "error", 0.0, detail="unparseable response")

        status = body.get("query_status")
        if status == "ok":
            url_count = len(body.get("urls") or [])
            return ReputationResult(
                self.name,
                "malicious",
                weight=0.5,
                category="malware_distribution",
                detail=f"URLhaus lists {url_count} malicious URL(s) hosted on this domain",
            )
        if status == "no_results":
            # Not found in URLhaus — this says nothing positive or negative;
            # it is not a threat-intel source for benign domains.
            return ReputationResult(self.name, "unknown", 0.0, detail="No URLhaus records")
        return ReputationResult(self.name, "error", 0.0, detail=f"Unexpected query_status: {status}")
