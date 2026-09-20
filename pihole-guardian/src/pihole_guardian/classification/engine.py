"""Classification engine: turns raw evidence (reputation-provider results +
local behavioral hints) into a single Category + confidence + explanation.

This stage does NOT decide whether to block anything — see decision/engine.py
for why that's a separate stage (requirement #5). This stage's only job is
"what does the evidence say this domain is", as honestly as possible,
including saying "conflicting" or "unknown" when that's the truth.

Scoring model (deliberately simple and auditable rather than a black-box
score, per requirement #5's "explain its reasoning"):
  - Each negative-verdict source's weight is capped at `max_single_source_weight`
    before being summed — this is what makes "one weak reputation signal
    should NOT automatically cause blocking" (#22) a structural property of
    the math, not just a threshold that happens to be set high enough today.
  - Two or more independent sources agreeing adds a flat agreement bonus.
  - Any legitimate-verdict evidence coexisting with negative evidence marks
    the classification as `conflict=True` and subtracts a conflict penalty
    from the negative confidence — evidence that disagrees with itself
    should produce a *less* confident answer, never get resolved by picking
    a side silently.
  - Query volume/popularity is never read here at all (see behavioral.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pihole_guardian.classification.categories import Category
from pihole_guardian.config import Settings
from pihole_guardian.reputation.base import ReputationResult

_NEGATIVE_VERDICTS = {"malicious", "suspicious", "tracking", "telemetry", "unwanted"}


@dataclass
class ClassificationResult:
    category: Category
    confidence: float
    hard_legitimate_match: bool
    legitimate_signals: list[str] = field(default_factory=list)
    malicious_signals: list[str] = field(default_factory=list)
    conflict: bool = False
    independent_negative_sources: int = 0
    reasoning: str = ""


def classify(
    evidence: list[ReputationResult],
    settings: Settings,
) -> ClassificationResult:
    negative_hits = [e for e in evidence if e.verdict in _NEGATIVE_VERDICTS]
    legitimate_hits = [e for e in evidence if e.verdict == "legitimate"]

    hard_legitimate = [e for e in legitimate_hits if e.weight >= 0.99]
    soft_legitimate = [e for e in legitimate_hits if e.weight < 0.99]

    legitimate_signals = [f"{e.source}: {e.detail}" for e in legitimate_hits]
    malicious_signals = [f"{e.source}: {e.detail}" for e in negative_hits]

    conflict = bool(negative_hits) and bool(legitimate_hits)

    # --- capped, source-diverse negative scoring ---
    capped = [min(e.weight, settings.max_single_source_weight) for e in negative_hits]
    negative_score = sum(capped)
    independent_sources = len({e.source for e in negative_hits})
    if independent_sources >= 2:
        negative_score += settings.source_agreement_bonus
    if conflict:
        negative_score = max(0.0, negative_score - settings.conflict_penalty)
    negative_score = min(negative_score, 1.0)

    # --- soft legitimate scoring (used only when there's no negative evidence) ---
    soft_legit_score = min(sum(min(e.weight, settings.max_single_source_weight) for e in soft_legitimate), 1.0)

    if hard_legitimate:
        reasoning = _build_reasoning(
            "Matches curated legitimate-infrastructure evidence; automatic blocking is disabled "
            "for this domain regardless of any other evidence.",
            legitimate_signals,
            malicious_signals,
            conflict,
        )
        return ClassificationResult(
            category=Category.LEGITIMATE_INFRASTRUCTURE,
            confidence=1.0,
            hard_legitimate_match=True,
            legitimate_signals=legitimate_signals,
            malicious_signals=malicious_signals,
            conflict=conflict,
            independent_negative_sources=independent_sources,
            reasoning=reasoning,
        )

    if not negative_hits:
        if soft_legitimate:
            return ClassificationResult(
                category=Category.KNOWN_LEGITIMATE,
                confidence=soft_legit_score,
                hard_legitimate_match=False,
                legitimate_signals=legitimate_signals,
                malicious_signals=[],
                conflict=False,
                independent_negative_sources=0,
                reasoning=_build_reasoning(
                    "Weak corroborating legitimate signals found, no negative evidence.",
                    legitimate_signals,
                    [],
                    False,
                ),
            )
        return ClassificationResult(
            category=Category.UNKNOWN,
            confidence=0.0,
            hard_legitimate_match=False,
            legitimate_signals=[],
            malicious_signals=[],
            conflict=False,
            independent_negative_sources=0,
            reasoning="No reputation source returned usable evidence for this domain. "
            "Unknown does not mean malicious — no action is warranted from classification alone.",
        )

    # Pick the dominant negative category by total capped weight per verdict type.
    per_verdict_weight: dict[str, float] = {}
    for e, w in zip(negative_hits, capped):
        per_verdict_weight[e.verdict] = per_verdict_weight.get(e.verdict, 0.0) + w
    dominant_verdict = max(per_verdict_weight, key=lambda k: per_verdict_weight[k])

    category = _category_for(dominant_verdict, negative_score, settings)
    reasoning = _build_reasoning(
        f"Dominant signal type: {dominant_verdict} (from {independent_sources} independent source(s)).",
        legitimate_signals,
        malicious_signals,
        conflict,
    )

    return ClassificationResult(
        category=category,
        confidence=negative_score,
        hard_legitimate_match=False,
        legitimate_signals=legitimate_signals,
        malicious_signals=malicious_signals,
        conflict=conflict,
        independent_negative_sources=independent_sources,
        reasoning=reasoning,
    )


def _category_for(dominant_verdict: str, confidence: float, settings: Settings) -> Category:
    if dominant_verdict == "malicious":
        return Category.KNOWN_MALICIOUS if confidence >= settings.block_confidence_threshold else Category.STRONGLY_SUSPICIOUS
    if dominant_verdict == "tracking":
        return Category.ADVERTISING_TRACKING
    if dominant_verdict == "telemetry":
        return Category.TELEMETRY_ANALYTICS
    if dominant_verdict == "unwanted":
        return Category.POTENTIALLY_UNWANTED
    if dominant_verdict == "suspicious":
        return Category.STRONGLY_SUSPICIOUS if confidence >= settings.monitor_confidence_threshold else Category.UNKNOWN
    return Category.UNKNOWN


def _build_reasoning(headline: str, legit: list[str], malicious: list[str], conflict: bool) -> str:
    parts = [headline]
    if conflict:
        parts.append(
            "Evidence conflicts: both legitimate and negative signals were found for this domain. "
            "Confidence has been reduced accordingly, per policy of preferring caution on conflicting evidence."
        )
    if malicious:
        parts.append("Negative evidence: " + "; ".join(malicious))
    if legit:
        parts.append("Legitimate-service indicators: " + "; ".join(legit))
    else:
        parts.append("Legitimate-service indicators: none found.")
    return " ".join(parts)
