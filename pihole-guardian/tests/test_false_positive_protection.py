"""Requirement #4: none of these signals, alone, may cause a block."""
from pihole_guardian.classification.behavioral import lexical_hint
from pihole_guardian.classification.engine import classify
from pihole_guardian.decision.engine import decide
from pihole_guardian.reputation.base import ReputationResult


def _decide_from_evidence(settings, evidence, currently_auto_blocked=False):
    classification = classify(evidence, settings)
    return decide(
        classification, is_protected=False, protected_reason=None,
        currently_auto_blocked=currently_auto_blocked, settings=settings,
    ), classification


def test_suspicious_sounding_name_alone_never_blocks(settings):
    hint = lexical_hint("telemetry.somevendor.example")
    assert hint is not None
    outcome, _ = _decide_from_evidence(settings, [hint])
    assert outcome.action in ("no_action", "monitor")


def test_single_low_reputation_source_never_blocks(settings):
    evidence = [ReputationResult("one_provider", "suspicious", 0.5, detail="low reputation score")]
    outcome, _ = _decide_from_evidence(settings, evidence)
    assert outcome.action != "block"


def test_newly_registered_domain_alone_never_blocks(settings):
    evidence = [ReputationResult("rdap", "suspicious", 0.15, category="newly_registered", detail="registered 2 days ago")]
    outcome, _ = _decide_from_evidence(settings, evidence)
    assert outcome.action != "block"


def test_telemetry_classification_still_requires_multiple_sources_to_block(settings):
    evidence = [ReputationResult("one_provider", "telemetry", 0.4, detail="looks like telemetry")]
    outcome, classification = _decide_from_evidence(settings, evidence)
    assert classification.independent_negative_sources == 1
    assert outcome.action != "block"


def test_two_strong_independent_sources_agreeing_is_block_eligible(settings):
    # Mirrors the worked example in the project brief: multiple independent,
    # substantial sources agreeing (not one strong + one negligible signal)
    # is what's meant to clear the block bar.
    evidence = [
        ReputationResult("tracker_list", "tracking", 0.45, detail="in bundled tracker list"),
        ReputationResult("virustotal", "tracking", 0.4, detail="vendor categories indicate tracking"),
        ReputationResult("behavioral_lexical", "tracking", 0.1, detail="lexical hint"),
    ]
    outcome, classification = _decide_from_evidence(settings, evidence)
    assert classification.independent_negative_sources == 3
    assert outcome.action == "block"
