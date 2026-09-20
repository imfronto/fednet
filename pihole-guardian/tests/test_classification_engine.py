from pihole_guardian.classification.categories import Category
from pihole_guardian.classification.engine import classify
from pihole_guardian.reputation.base import ReputationResult


def _malicious(source: str, weight: float = 0.4) -> ReputationResult:
    return ReputationResult(source, "malicious", weight, category="threat_intel", detail=f"{source} says malicious")


def _legit(source: str = "legitimate_infra", weight: float = 1.0) -> ReputationResult:
    return ReputationResult(source, "legitimate", weight, category="cdn", detail=f"{source} says legitimate")


def test_no_evidence_is_unknown_not_malicious(settings):
    result = classify([], settings)
    assert result.category == Category.UNKNOWN
    assert result.confidence == 0.0


def test_single_weak_signal_never_reaches_block_eligible_confidence(settings):
    # One provider, high stated weight — but classification alone doesn't
    # gate blocking (the decision engine's independent-source count does);
    # this test only asserts the *confidence math* caps a single source.
    result = classify([_malicious("only_source", weight=0.95)], settings)
    assert result.confidence <= settings.max_single_source_weight + 1e-9


def test_two_independent_sources_agree_and_raise_confidence(settings):
    result = classify([_malicious("provider_a"), _malicious("provider_b")], settings)
    assert result.independent_negative_sources == 2
    assert result.confidence > settings.max_single_source_weight  # agreement bonus applied
    assert result.category in (Category.KNOWN_MALICIOUS, Category.STRONGLY_SUSPICIOUS)


def test_hard_legitimate_match_wins_even_with_negative_evidence(settings):
    evidence = [_malicious("tracker_list", weight=0.5), _legit(weight=1.0)]
    result = classify(evidence, settings)
    assert result.category == Category.LEGITIMATE_INFRASTRUCTURE
    assert result.hard_legitimate_match is True
    assert result.conflict is True  # conflict is recorded even though protection wins
    assert result.confidence == 1.0


def test_conflicting_soft_evidence_reduces_confidence(settings):
    with_conflict = classify([_malicious("a"), _malicious("b"), ReputationResult("soft_legit", "legitimate", 0.2)], settings)
    without_conflict = classify([_malicious("a"), _malicious("b")], settings)
    assert with_conflict.conflict is True
    assert with_conflict.confidence < without_conflict.confidence


def test_error_and_unknown_verdicts_never_contribute_to_score(settings):
    evidence = [
        ReputationResult("provider_a", "error", 0.0, detail="timed out"),
        ReputationResult("provider_b", "unknown", 0.0, detail="no data"),
    ]
    result = classify(evidence, settings)
    assert result.category == Category.UNKNOWN
    assert result.confidence == 0.0


def test_dominant_verdict_type_determines_category():
    from pihole_guardian.config import Settings

    settings = Settings(dashboard_password="x", api_token="x", max_single_source_weight=0.5, source_agreement_bonus=0.1)
    tracking = classify(
        [ReputationResult("a", "tracking", 0.4), ReputationResult("b", "tracking", 0.4)], settings
    )
    assert tracking.category == Category.ADVERTISING_TRACKING
