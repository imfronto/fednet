from pihole_guardian.domain_utils import is_subdomain_or_equal, matches_wildcard, normalize_domain


def test_normalizes_case_and_trailing_dot():
    n = normalize_domain("Example.COM.")
    assert n.valid
    assert n.domain == "example.com"


def test_strips_leading_wildcard_marker():
    n = normalize_domain("*.example.com")
    assert n.valid
    assert n.domain == "example.com"


def test_idna_punycode_roundtrip():
    n = normalize_domain("xn--4ca.com")  # punycode for "ä.com"
    assert n.valid
    assert n.domain == "xn--4ca.com"
    assert "ä" in n.unicode


def test_unicode_domain_is_encoded_to_punycode():
    n = normalize_domain("ä.com")
    assert n.valid
    assert n.domain.startswith("xn--")


def test_rejects_empty_and_malformed():
    assert not normalize_domain("").valid
    assert not normalize_domain("not a domain").valid
    assert not normalize_domain("nodothere").valid


def test_registrable_domain_extraction():
    n = normalize_domain("api.example.co.uk")
    assert n.valid
    assert n.registrable_domain == "example.co.uk"


def test_subdomain_matching():
    assert is_subdomain_or_equal("api.example.com", "example.com")
    assert is_subdomain_or_equal("example.com", "example.com")
    assert not is_subdomain_or_equal("notexample.com", "example.com")
    assert not is_subdomain_or_equal("example.com.evil.com", "example.com")


def test_wildcard_matching_excludes_apex():
    assert matches_wildcard("sub.example.com", "*.example.com")
    assert not matches_wildcard("example.com", "*.example.com")
    assert matches_wildcard("deep.sub.example.com", "*.example.com")
