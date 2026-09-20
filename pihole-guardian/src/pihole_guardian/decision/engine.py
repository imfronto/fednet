"""Decision engine: turns a classification into an action, applying every
protection rule from requirement #22 as an explicit, ordered check —
deliberately not a single score threshold. See docs/ARCHITECTURE.md for the
full write-up; the short version:

  1. Manual protected-list membership wins, unconditionally, before anything
     else is even inspected.
  2. A hard legitimate-infrastructure classification wins next.
  3. Conflicting evidence (both negative and legitimate signals present)
     downgrades straight to `flag_for_review` — never `block`.
  4. Blocking additionally requires the classification to have cleared
     `min_independent_sources_for_block` distinct corroborating sources,
     even if the math alone would already be above the confidence
     threshold — a structural floor under "one weak signal is never enough."
  5. Everything below the monitor threshold is left alone: unknown is not
     treated as malicious, and low confidence is not treated as guilty.

`gate_for_mode` is a second, independent step: it takes the action this
module decided is *warranted by the evidence* and asks a completely
different question — given the operator's configured mode (AUDIT/DRY_RUN/
RECOMMEND/ENFORCE), is this actually allowed to happen automatically right
now, or does a human need to approve it first? Keeping these separate means
raising the mode to ENFORCE can never change *what* the system believes
about a domain, only *what it's allowed to do* about that belief.
"""
from __future__ import annotations

from dataclasses import dataclass

from pihole_guardian.classification.categories import NEGATIVE_CATEGORIES, Category
from pihole_guardian.classification.engine import ClassificationResult
from pihole_guardian.config import Mode, Settings

Action = str  # "block" | "unblock" | "monitor" | "no_action" | "flag_for_review"


@dataclass
class DecisionResult:
    action: Action
    confidence: float
    reason: str


def decide(
    classification: ClassificationResult,
    *,
    is_protected: bool,
    protected_reason: str | None,
    currently_auto_blocked: bool,
    settings: Settings,
) -> DecisionResult:
    # --- 1. manual protected list: absolute, checked before anything else ---
    if is_protected:
        if currently_auto_blocked:
            return DecisionResult(
                "unblock",
                1.0,
                "Domain matches the manually managed protected list "
                f"({protected_reason or 'no reason recorded'}); protected domains must never remain "
                "auto-blocked, so the automatic block is being removed regardless of any other evidence.",
            )
        return DecisionResult(
            "no_action",
            1.0,
            "Domain matches the manually managed protected list "
            f"({protected_reason or 'no reason recorded'}); automatic blocking is permanently disabled "
            "for it.",
        )

    # --- 2. hard legitimate-infrastructure match: wins over everything else ---
    if classification.hard_legitimate_match:
        if currently_auto_blocked:
            return DecisionResult(
                "unblock",
                classification.confidence,
                "Domain matches curated legitimate-infrastructure evidence. " + classification.reasoning,
            )
        return DecisionResult(
            "no_action",
            classification.confidence,
            "Domain matches curated legitimate-infrastructure evidence; never eligible for automatic "
            "blocking. " + classification.reasoning,
        )

    # --- 3. conflicting evidence: never block, surface for a human instead ---
    if classification.conflict and classification.category in NEGATIVE_CATEGORIES:
        return DecisionResult(
            "flag_for_review",
            classification.confidence,
            "Evidence conflicts (both negative and legitimate-service indicators were found). "
            "Per policy, conflicting evidence is never auto-blocked. " + classification.reasoning,
        )

    # --- 4. domain no longer looks negative but we auto-blocked it before ---
    if currently_auto_blocked and classification.category not in NEGATIVE_CATEGORIES:
        return DecisionResult(
            "unblock",
            classification.confidence,
            "This domain was previously auto-blocked, but current evidence no longer supports that: "
            + classification.reasoning,
        )

    # --- 5. negative categories: block/monitor/no_action based on confidence + source count ---
    if classification.category in NEGATIVE_CATEGORIES:
        if classification.independent_negative_sources < settings.min_independent_sources_for_block:
            if classification.confidence >= settings.monitor_confidence_threshold:
                return DecisionResult(
                    "monitor",
                    classification.confidence,
                    f"Confidence ({classification.confidence:.0%}) is high enough to monitor, but only "
                    f"{classification.independent_negative_sources} independent source(s) corroborate it "
                    f"(minimum {settings.min_independent_sources_for_block} required for a block). "
                    + classification.reasoning,
                )
            return DecisionResult(
                "no_action",
                classification.confidence,
                "Insufficient independent corroboration and confidence below the monitor threshold; "
                "left untouched. " + classification.reasoning,
            )
        if classification.confidence >= settings.block_confidence_threshold:
            return DecisionResult(
                "block",
                classification.confidence,
                f"Confidence ({classification.confidence:.0%}) meets the block threshold "
                f"({settings.block_confidence_threshold:.0%}) with "
                f"{classification.independent_negative_sources} independent corroborating source(s) and "
                "no legitimate-service indicators. " + classification.reasoning,
            )
        if classification.confidence >= settings.monitor_confidence_threshold:
            return DecisionResult(
                "monitor",
                classification.confidence,
                f"Confidence ({classification.confidence:.0%}) is below the block threshold "
                f"({settings.block_confidence_threshold:.0%}); flagged for monitoring, not blocking. "
                + classification.reasoning,
            )
        return DecisionResult(
            "no_action",
            classification.confidence,
            "Low-confidence/unknown evidence is left untouched — unknown does not mean malicious. "
            + classification.reasoning,
        )

    # --- category is UNKNOWN or KNOWN_LEGITIMATE and nothing is currently blocked ---
    return DecisionResult("no_action", classification.confidence, classification.reasoning)


def gate_for_mode(action: Action, confidence: float, mode: Mode, settings: Settings) -> tuple[str, bool]:
    """Returns (status, should_auto_apply_now).

    status is one of:
      'observed'  — recorded for visibility only; nothing pending, nothing applied (AUDIT, and any
                    non-mutating action in every mode)
      'simulated' — DRY_RUN: exactly what would happen is recorded, nothing is applied
      'pending'   — RECOMMEND, or an ENFORCE-eligible action that didn't clear the automatic bar;
                    requires explicit human approval via `service review`
      'auto'      — ENFORCE: applied immediately
    """
    if action in ("no_action", "monitor", "flag_for_review"):
        return "observed", False

    # action is "block" or "unblock" from here on.
    if mode == Mode.AUDIT:
        return "observed", False
    if mode == Mode.DRY_RUN:
        return "simulated", False
    if mode == Mode.RECOMMEND:
        return "pending", False
    if mode == Mode.ENFORCE:
        if action == "unblock":
            # Removing an automatic block is the safety-preferred direction —
            # leaving a wrongly-blocked domain blocked is the worse failure
            # mode, so this is never throttled behind manual approval.
            return "auto", True
        if confidence >= settings.block_confidence_threshold:
            return "auto", True
        return "pending", False
    return "observed", False
