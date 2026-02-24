"""Tests for pipeline-level report citation filtering and finalization."""

from agents.reporter_agent import ReporterAgent
from core.config import settings
from service.citation_manager import CitationManager
from service.data_structures import ScholarCitation
from service.pipeline import ResearchPipeline


def test_select_report_citation_ids_filters_sensitive_sources(monkeypatch) -> None:
    """Pipeline should drop sensitive web citations and keep academic evidence."""

    manager = CitationManager("research_test")
    bad = ScholarCitation(
        citation_id="CIT-B001-01",
        title="Shadowrocket 节点合集",
        url="https://openclash.cc/free-node/test",
        snippet="clash v2ray shadowrocket",
        source_type="web",
    )
    good = ScholarCitation(
        citation_id="CIT-B001-02",
        title="GNN DRL MEC Offloading",
        url="https://www.semanticscholar.org/paper/abc",
        snippet="task offloading in mobile edge computing",
        source_type="paper",
    )
    manager.add_citation(bad)
    manager.add_citation(good)

    monkeypatch.setattr(settings, "REPORT_REFERENCES_MAX_TOTAL", 20, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_DOMAIN_ALLOWLIST", "", raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_DOMAIN_DENYLIST", "", raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_BLOCKED_TERMS", "", raising=False)

    pipeline = ResearchPipeline("http://localhost:8000", "/tmp/deep_research", 10)
    selected_ids, stats = pipeline._select_report_citation_ids(
        topic="GNN DRL in edge computing",
        citation_manager=manager,
    )

    assert good.citation_id in selected_ids
    assert bad.citation_id not in selected_ids
    assert stats["citations_total_before_filter"] == 2
    assert stats["citations_total_after_filter"] == 1


def test_finalize_report_markdown_rebuilds_filtered_references(monkeypatch) -> None:
    """Finalizer should keep only used refs and rebuild the references section."""

    manager = CitationManager("research_test")
    c1 = ScholarCitation(
        citation_id="CIT-B001-01",
        title="Paper One",
        url="https://arxiv.org/abs/1234.1",
        snippet="s1",
        source_type="paper",
    )
    c2 = ScholarCitation(
        citation_id="CIT-B001-02",
        title="Paper Two",
        url="https://arxiv.org/abs/1234.2",
        snippet="s2",
        source_type="paper",
    )
    c3 = ScholarCitation(
        citation_id="CIT-B001-03",
        title="Paper Three",
        url="https://arxiv.org/abs/1234.3",
        snippet="s3",
        source_type="paper",
    )
    manager.add_citation(c1)
    manager.add_citation(c2)
    manager.add_citation(c3)
    manager.build_ref_map_for([c1.citation_id, c2.citation_id, c3.citation_id])

    monkeypatch.setattr(settings, "REPORT_REFERENCES_MAX_TOTAL", 2, raising=False)
    monkeypatch.setattr(settings, "REPORT_REFERENCES_MAX_PER_SECTION", 2, raising=False)

    reporter = ReporterAgent(manager, language="en")
    pipeline = ResearchPipeline("http://localhost:8000", "/tmp/deep_research", 10)
    raw_report = (
        "# DeepResearch Report\n\n"
        "## Section A\n"
        "Alpha [[1]](#ref-1) and [[2]](#ref-2).\n\n"
        "## Section B\n"
        "Beta [[3]](#ref-3).\n\n"
        "## References\n"
        "<a id=\"ref-999\"></a>[999] Old\n"
    )

    finalized, used_refs = pipeline._finalize_report_markdown(
        report_markdown=raw_report,
        reporter=reporter,
        allowed_refs=[1, 2, 3],
    )

    assert used_refs == [1, 2]
    assert "[[3]](#ref-3)" not in finalized
    assert "<a id=\"ref-1\"></a>[1]" in finalized
    assert "<a id=\"ref-2\"></a>[2]" in finalized
    assert "<a id=\"ref-3\"></a>[3]" not in finalized


def test_select_report_citation_ids_applies_stricter_overlap_for_web_sources(monkeypatch) -> None:
    """Weakly related web citations should be dropped by stricter web overlap gate."""

    manager = CitationManager("research_test")
    paper = ScholarCitation(
        citation_id="CIT-B002-01",
        title="GNN-DRL for Edge Offloading",
        url="https://arxiv.org/abs/2401.12345",
        snippet="Joint graph reinforcement learning for edge resource allocation.",
        source_type="paper",
    )
    weak_web = ScholarCitation(
        citation_id="CIT-B002-02",
        title="Edge market outlook for telecom vendors",
        url="https://example.org/edge-outlook",
        snippet="Industry investment and vendor strategy analysis.",
        source_type="web",
    )
    manager.add_citation(paper)
    manager.add_citation(weak_web)

    monkeypatch.setattr(settings, "REPORT_REFERENCES_MAX_TOTAL", 20, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_MIN_QUALITY_SCORE", 0.4, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_MIN_QUERY_OVERLAP", 0.12, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_WEB_MIN_QUERY_OVERLAP", 0.18, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_DOMAIN_ALLOWLIST", "", raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_DOMAIN_DENYLIST", "", raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_BLOCKED_TERMS", "", raising=False)

    pipeline = ResearchPipeline("http://localhost:8000", "/tmp/deep_research", 10)
    selected_ids, stats = pipeline._select_report_citation_ids(
        topic="edge computing optimization with gnn drl offloading",
        citation_manager=manager,
    )

    assert paper.citation_id in selected_ids
    assert weak_web.citation_id not in selected_ids
    assert stats["citation_drop_reasons"].get("low_overlap", 0) >= 1


def test_select_report_citation_ids_uses_relaxed_fallback_when_strict_rejects_all(monkeypatch) -> None:
    """Pipeline should use relaxed fallback instead of unrestricted fallback."""

    manager = CitationManager("research_test")
    medium = ScholarCitation(
        citation_id="CIT-B003-01",
        title="Edge offloading with graph reinforcement learning",
        url="https://example.edu/papers/edge-gnn-drl",
        snippet="GNN and DRL are combined for offloading under dynamic edge workloads.",
        source_type="paper",
    )
    manager.add_citation(medium)

    monkeypatch.setattr(settings, "REPORT_REFERENCES_MAX_TOTAL", 10, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_MIN_QUALITY_SCORE", 4.5, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_MIN_QUERY_OVERLAP", 0.2, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_RELAXED_MIN_QUALITY_SCORE", 0.5, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_RELAXED_MIN_QUERY_OVERLAP", 0.08, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_DOMAIN_ALLOWLIST", "", raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_DOMAIN_DENYLIST", "", raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_BLOCKED_TERMS", "", raising=False)

    pipeline = ResearchPipeline("http://localhost:8000", "/tmp/deep_research", 10)
    selected_ids, stats = pipeline._select_report_citation_ids(
        topic="gnn drl for edge computing offloading optimization",
        citation_manager=manager,
    )

    assert selected_ids == [medium.citation_id]
    assert stats["citation_filter_mode"] == "relaxed_fallback"


def test_select_report_citation_ids_can_fill_min_distinct_with_relaxed_pool(monkeypatch) -> None:
    """Strict pool should be supplemented by relaxed pool to reach minimum distinct refs."""

    manager = CitationManager("research_test")
    strict_ok = ScholarCitation(
        citation_id="CIT-B004-01",
        title="Edge offloading with GNN DRL",
        url="https://arxiv.org/abs/2501.00001",
        snippet="gnn drl offloading and edge resource allocation",
        source_type="paper",
    )
    relaxed_ok = ScholarCitation(
        citation_id="CIT-B004-02",
        title="Edge scheduling survey",
        url="https://example.edu/edge-scheduling",
        snippet="edge scheduling and distributed resource management",
        source_type="web",
    )
    manager.add_citation(strict_ok)
    manager.add_citation(relaxed_ok)

    monkeypatch.setattr(settings, "REPORT_REFERENCES_MAX_TOTAL", 10, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_DISTINCT_CITATIONS", 2, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_MIN_QUALITY_SCORE", 2.3, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_MIN_QUERY_OVERLAP", 0.12, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_WEB_MIN_QUERY_OVERLAP", 0.24, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_RELAXED_MIN_QUALITY_SCORE", 0.4, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_RELAXED_MIN_QUERY_OVERLAP", 0.05, raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_DOMAIN_ALLOWLIST", "", raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_DOMAIN_DENYLIST", "", raising=False)
    monkeypatch.setattr(settings, "REPORT_CITATION_BLOCKED_TERMS", "", raising=False)

    pipeline = ResearchPipeline("http://localhost:8000", "/tmp/deep_research", 10)
    selected_ids, stats = pipeline._select_report_citation_ids(
        topic="gnn drl in edge computing offloading",
        citation_manager=manager,
    )

    assert strict_ok.citation_id in selected_ids
    assert relaxed_ok.citation_id in selected_ids
    assert stats["citation_filter_mode"] == "strict_with_relaxed_fill"
