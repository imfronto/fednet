"""Reputation provider abstraction. Every source of evidence about a domain
(threat intel, curated legitimate-infra list, WHOIS/RDAP, etc.) implements
this interface, so providers can be added or removed without touching the
classification engine.

The one rule every implementation must follow: a provider that cannot reach
its backend, times out, or gets a response it doesn't understand returns
verdict='error' (or 'unknown' for "checked successfully, has no opinion").
It must never turn "I couldn't verify this" into verdict='malicious' — that
inversion is exactly the failure mode requirement #17 and #22 forbid.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

VALID_VERDICTS = {
    "malicious",
    "suspicious",
    "tracking",
    "telemetry",
    "unwanted",
    "legitimate",
    "unknown",
    "error",
}


@dataclass(frozen=True)
class ReputationResult:
    source: str
    verdict: str
    weight: float  # this provider's own confidence in the verdict, 0..1
    category: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.verdict not in VALID_VERDICTS:
            raise ValueError(f"Invalid verdict '{self.verdict}' from provider '{self.source}'")


class ReputationProvider(ABC):
    name: str = "unnamed"

    @abstractmethod
    def check(self, domain: str, registrable_domain: str) -> ReputationResult: ...

    def is_available(self) -> bool:
        """Whether this provider is configured/usable at all (e.g. has an API
        key). Unavailable providers are skipped entirely rather than counted
        as an 'unknown' vote, so they don't dilute source-agreement counts."""
        return True
