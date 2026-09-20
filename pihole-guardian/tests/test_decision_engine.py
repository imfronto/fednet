from pihole_guardian.classification.categories import Category
from pihole_guardian.classification.engine import ClassificationResult
from pihole_guardian.config import Mode
from pihole_guardian.decision.engine import decide, gate_for_mode


def _malicious_result(confidence=0.95, sources=2, conflict=False) -> ClassificationResult:
    return ClassificationResult(
        category=Category.KNOWN_MALICIOUS,
        confidence=confidence,
        hard_legitimate_match=False,
        legitimate_signals=[],
        malicious_signals=["a: bad", "b: bad"],
        conflict=conflict,
        independent_negative_sources=sources,
        reasoning="test",
    )


def test_protected_domain_never_blocked_regardless_of_evidence(settings):
    outcome = decide(
        _malicious_result(confidence=0.99, sources=5),
        is_protected=True,
        protected_reason="critical infra",
        currently_auto_blocked=False,
        settings=settings,
    )
    assert outcome.action == "no_action"


def test_protected_domain_gets_unblocked_if_previously_auto_blocked(settings):
    outcome = decide(
        _malicious_result(),
        is_protected=True,
        protected_reason="added to protected list after being blocked",
        currently_auto_blocked=True,
        settings=settings,
    )
    assert outcome.action == "unblock"


def test_hard_legitimate_classification_never_blocked(settings):
    result = ClassificationResult(
        category=Category.LEGITIMATE_INFRASTRUCTURE, confidence=1.0, hard_legitimate_match=True,
        legitimate_signals=["matches curated list"], malicious_signals=["x: bad"], conflict=True,
        independent_negative_sources=1, reasoning="test",
    )
    outcome = decide(result, is_protected=False, protected_reason=None, currently_auto_blocked=False, settings=settings)
    assert outcome.action == "no_action"


def test_conflicting_evidence_flags_for_review_never_blocks(settings):
    outcome = decide(
        _malicious_result(confidence=0.95, sources=2, conflict=True),
        is_protected=False, protected_reason=None, currently_auto_blocked=False, settings=settings,
    )
    assert outcome.action == "flag_for_review"


def test_insufficient_independent_sources_never_blocks(settings):
    outcome = decide(
        _malicious_result(confidence=0.95, sources=1),  # only one source, even though confidence is high
        is_protected=False, protected_reason=None, currently_auto_blocked=False, settings=settings,
    )
    assert outcome.action == "monitor"


def test_high_confidence_multi_source_is_block_eligible(settings):
    outcome = decide(
        _malicious_result(confidence=0.95, sources=2),
        is_protected=False, protected_reason=None, currently_auto_blocked=False, settings=settings,
    )
    assert outcome.action == "block"


def test_low_confidence_is_left_untouched(settings):
    result = ClassificationResult(
        category=Category.POTENTIALLY_UNWANTED, confidence=0.2, hard_legitimate_match=False,
        legitimate_signals=[], malicious_signals=["a: weak"], conflict=False,
        independent_negative_sources=1, reasoning="test",
    )
    outcome = decide(result, is_protected=False, protected_reason=None, currently_auto_blocked=False, settings=settings)
    assert outcome.action == "no_action"


def test_unknown_category_previously_blocked_gets_unblocked(settings):
    result = ClassificationResult(
        category=Category.UNKNOWN, confidence=0.0, hard_legitimate_match=False,
        legitimate_signals=[], malicious_signals=[], conflict=False,
        independent_negative_sources=0, reasoning="evidence no longer available",
    )
    outcome = decide(result, is_protected=False, protected_reason=None, currently_auto_blocked=True, settings=settings)
    assert outcome.action == "unblock"


def test_gate_for_mode_audit_never_mutates(settings):
    status, apply = gate_for_mode("block", 0.99, Mode.AUDIT, settings)
    assert status == "observed"
    assert apply is False


def test_gate_for_mode_dry_run_simulates_only(settings):
    status, apply = gate_for_mode("block", 0.99, Mode.DRY_RUN, settings)
    assert status == "simulated"
    assert apply is False


def test_gate_for_mode_recommend_requires_approval(settings):
    status, apply = gate_for_mode("block", 0.99, Mode.RECOMMEND, settings)
    assert status == "pending"
    assert apply is False


def test_gate_for_mode_enforce_auto_applies_high_confidence_block(settings):
    status, apply = gate_for_mode("block", 0.95, Mode.ENFORCE, settings)
    assert status == "auto"
    assert apply is True


def test_gate_for_mode_enforce_defers_low_confidence_block_to_review(settings):
    status, apply = gate_for_mode("block", 0.5, Mode.ENFORCE, settings)
    assert status == "pending"
    assert apply is False


def test_gate_for_mode_enforce_always_allows_unblock_immediately(settings):
    # Even a low-confidence unblock proceeds immediately — leaving a bad
    # block in place is the worse failure mode (requirement #22).
    status, apply = gate_for_mode("unblock", 0.1, Mode.ENFORCE, settings)
    assert status == "auto"
    assert apply is True


def test_gate_for_mode_never_mutates_for_non_mutating_actions(settings):
    for mode in Mode:
        status, apply = gate_for_mode("monitor", 0.99, mode, settings)
        assert status == "observed"
        assert apply is False
