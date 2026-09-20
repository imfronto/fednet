from pihole_guardian.reputation.aggregator import ReputationAggregator
from pihole_guardian.reputation.legitimate_infra import LegitimateInfraProvider
from tests.fakes import FixedResultProvider, RaisingProvider, SlowProvider
from pihole_guardian.reputation.base import ReputationResult


def test_legitimate_infra_matches_curated_domain(settings):
    provider = LegitimateInfraProvider(settings.legitimate_infra_path)
    result = provider.check("pypi.org", "pypi.org")
    assert result.verdict == "legitimate"
    assert result.weight == 1.0


def test_legitimate_infra_matches_subdomain_of_curated_entry(settings):
    provider = LegitimateInfraProvider(settings.legitimate_infra_path)
    result = provider.check("mirrors.pypi.org", "pypi.org")
    assert result.verdict == "legitimate"


def test_legitimate_infra_unrelated_domain_is_unknown(settings):
    provider = LegitimateInfraProvider(settings.legitimate_infra_path)
    result = provider.check("totally-unrelated-domain.example", "totally-unrelated-domain.example")
    assert result.verdict == "unknown"


def test_aggregator_never_turns_a_bug_into_a_malicious_verdict():
    aggregator = ReputationAggregator([RaisingProvider()], timeout_seconds=1.0)
    results = aggregator.gather("example.com", "example.com")
    assert len(results) == 1
    assert results[0].verdict == "error"


def test_aggregator_never_turns_a_timeout_into_a_malicious_verdict():
    aggregator = ReputationAggregator([SlowProvider(delay_seconds=0.5)], timeout_seconds=0.05)
    results = aggregator.gather("example.com", "example.com")
    assert len(results) == 1
    assert results[0].verdict == "error"


def test_unavailable_provider_is_skipped_not_counted_as_unknown():
    fixed = FixedResultProvider(
        "unavailable", ReputationResult("unavailable", "malicious", 0.9), available=False
    )
    aggregator = ReputationAggregator([fixed], timeout_seconds=1.0)
    results = aggregator.gather("example.com", "example.com")
    assert results == []


def test_provider_failure_is_isolated_from_other_providers():
    good = FixedResultProvider("good", ReputationResult("good", "malicious", 0.4))
    aggregator = ReputationAggregator([RaisingProvider(), good], timeout_seconds=1.0)
    results = aggregator.gather("example.com", "example.com")
    verdicts = {r.source: r.verdict for r in results}
    assert verdicts["good"] == "malicious"
    assert verdicts["raising_provider"] == "error"
