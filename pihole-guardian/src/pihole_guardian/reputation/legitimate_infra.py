from __future__ import annotations

import logging
from pathlib import Path

import yaml

from pihole_guardian.domain_utils import is_subdomain_or_equal
from pihole_guardian.reputation.base import ReputationProvider, ReputationResult

logger = logging.getLogger(__name__)


class LegitimateInfraProvider(ReputationProvider):
    """Checks a domain against the bundled curated list of legitimate
    infrastructure (OS/package-manager/CDN/auth/NTP/cert/DNS/etc. — see
    resources/legitimate_infra.yml). This is the primary mechanical enforcement
    of requirement #4's false-positive protection list."""

    name = "legitimate_infra"

    def __init__(self, path: Path) -> None:
        self._by_domain: dict[str, str] = {}
        try:
            raw = yaml.safe_load(path.read_text()) or {}
            for category, domains in raw.items():
                for d in domains or []:
                    self._by_domain[str(d).lower()] = category
        except (OSError, yaml.YAMLError) as exc:
            logger.error("Failed to load legitimate-infra list from %s: %s", path, exc)

    def check(self, domain: str, registrable_domain: str) -> ReputationResult:
        for entry, category in self._by_domain.items():
            if is_subdomain_or_equal(domain, entry):
                return ReputationResult(
                    source=self.name,
                    verdict="legitimate",
                    weight=1.0,
                    category=category,
                    detail=f"Matches curated legitimate-infrastructure entry '{entry}' ({category})",
                )
        return ReputationResult(source=self.name, verdict="unknown", weight=0.0, detail="No match")
