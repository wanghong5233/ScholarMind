"""Tests for domain pattern matching edge cases."""

from service.citation_quality import matches_domain_pattern


def test_domain_pattern_matching_requires_exact_or_subdomain() -> None:
    """Suffix collisions like scam.org vs acm.org must not match."""

    assert matches_domain_pattern("acm.org", ["acm.org"])
    assert matches_domain_pattern("dl.acm.org", ["acm.org"])
    assert not matches_domain_pattern("scam.org", ["acm.org"])
