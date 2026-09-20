"""Local, network-free behavioral/lexical signals.

Deliberately excluded, by design: raw query popularity (query_count) as
either a positive ('frequently queried = trustworthy') or negative
('rarely queried = suspicious') signal — the project brief explicitly
forbids both directions. Nothing in this module reads `query_count` for
scoring purposes; it exists here only for the domain's display context.

What IS used: hostname tokens conventionally associated with tracking/
telemetry infrastructure (e.g. "track.", "telemetry.", "px.", "beacon."). A
domain's name is one of the "do not block merely because" signals in
requirement #4, so this contributes at a weight (0.1) capped well below
what any block decision requires by itself, and is presented as 'behavioral'
evidence like any other source, subject to the same independent-source and
conflict rules everything else goes through in the classification engine.
"""
from __future__ import annotations

import re

from pihole_guardian.reputation.base import ReputationResult

_TRACKING_TOKENS = re.compile(
    r"(^|[.-])(track(ing)?|telemetry|analytics|metrics|beacon|px|pixel|adserver|ads?[0-9]*|"
    r"collector|logging?)([.-]|$)"
)


def lexical_hint(domain: str) -> ReputationResult | None:
    if _TRACKING_TOKENS.search(domain):
        return ReputationResult(
            source="behavioral_lexical",
            verdict="tracking",
            weight=0.1,
            category="lexical_pattern",
            detail="Hostname contains a token conventionally associated with tracking/telemetry "
            "infrastructure — weak signal, never sufficient alone",
        )
    return None
