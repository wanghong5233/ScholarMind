"""Tests for web-search quality filtering guardrails."""

from service.tools.web_search_tool import WebSearchTool


def test_web_quality_filter_drops_blocked_domains_without_raw_fallback() -> None:
    """Blocked domains must not leak through non-academic fallback paths."""

    results = [
        {
            "title": "Shadowrocket 节点合集",
            "url": "https://openclash.cc/free-node/2026-2-20-free-v2ray-subscribe.htm",
            "snippet": "v2ray clash shadowrocket",
        }
    ]

    ranked = WebSearchTool._rank_results_by_source_quality(
        results=results,
        query="how to speed up internet",
        max_results=5,
        domain_allowlist=[],
        domain_denylist=["openclash.cc"],
        blocked_terms=["shadowrocket", "v2ray"],
        min_quality_score=0.6,
        min_query_overlap=0.08,
    )

    assert ranked == []


def test_web_quality_filter_keeps_academic_result() -> None:
    """High-signal academic domains should pass quality filtering."""

    results = [
        {
            "title": "Task Offloading in Edge Computing Using GNNs and DQN",
            "url": "https://arxiv.org/abs/2401.01140",
            "snippet": "mobile edge computing task offloading reinforcement learning",
        }
    ]

    ranked = WebSearchTool._rank_results_by_source_quality(
        results=results,
        query="GNN DRL mobile edge computing paper benchmark",
        max_results=5,
        domain_allowlist=[],
        domain_denylist=[],
        blocked_terms=[],
        min_quality_score=0.6,
        min_query_overlap=0.08,
    )

    assert len(ranked) == 1
    assert "arxiv.org" in str(ranked[0].get("url") or "")


def test_web_quality_filter_uses_academic_allowlist_for_academic_queries() -> None:
    """Academic queries should not retain generic domains when allowlist is empty."""

    results = [
        {
            "title": "Blog post",
            "url": "https://blog.example.com/post",
            "snippet": "random engineering notes",
        }
    ]

    ranked = WebSearchTool._rank_results_by_source_quality(
        results=results,
        query="GNN DRL edge computing paper survey",
        max_results=5,
        domain_allowlist=[],
        domain_denylist=[],
        blocked_terms=[],
        min_quality_score=0.6,
        min_query_overlap=0.08,
    )

    assert ranked == []
