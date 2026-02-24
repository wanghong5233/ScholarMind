"""Tests for DeepResearch report quality gates."""

import pytest

from core.config import settings
from service.pipeline import ResearchPipeline


def test_report_quality_gate_passes_with_sufficient_metrics(monkeypatch):
    """Quality gate should pass when report metrics satisfy thresholds."""

    monkeypatch.setattr(settings, "REPORT_MIN_COMPLETED_BLOCKS", 1, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_PARAGRAPHS_TOTAL", 6, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_DISTINCT_CITATIONS", 2, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_CITATION_PARAGRAPH_COVERAGE", 0.2, raising=False)

    ResearchPipeline._validate_report_quality(
        report_quality={
            "paragraphs_total": 8,
            "citations_mentions": 9,
            "citations_distinct_count": 3,
            "citation_paragraph_coverage": 0.56,
        },
        allowed_refs=[1, 2, 3],
        completed_blocks=2,
    )


def test_report_quality_gate_fails_on_low_coverage(monkeypatch):
    """Quality gate should fail when citation coverage is too low."""

    monkeypatch.setattr(settings, "REPORT_MIN_COMPLETED_BLOCKS", 1, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_PARAGRAPHS_TOTAL", 4, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_DISTINCT_CITATIONS", 1, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_CITATION_PARAGRAPH_COVERAGE", 0.25, raising=False)

    with pytest.raises(RuntimeError, match="citation_paragraph_coverage"):
        ResearchPipeline._validate_report_quality(
            report_quality={
                "paragraphs_total": 6,
                "citations_mentions": 4,
                "citations_distinct_count": 2,
                "citation_paragraph_coverage": 0.1,
            },
            allowed_refs=[1, 2],
            completed_blocks=1,
        )


def test_report_quality_gate_fails_when_high_quality_refs_insufficient(monkeypatch):
    """Quality gate should fail when selected high-quality refs are below threshold."""

    monkeypatch.setattr(settings, "REPORT_MIN_COMPLETED_BLOCKS", 1, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_PARAGRAPHS_TOTAL", 4, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_DISTINCT_CITATIONS", 2, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_CITATION_PARAGRAPH_COVERAGE", 0.2, raising=False)

    with pytest.raises(RuntimeError, match="high_quality_refs=1 < 2"):
        ResearchPipeline._validate_report_quality(
            report_quality={
                "paragraphs_total": 6,
                "citations_mentions": 3,
                "citations_distinct_count": 1,
                "citation_paragraph_coverage": 0.4,
            },
            allowed_refs=[1],
            completed_blocks=1,
        )


def test_report_quality_gate_fails_when_no_high_quality_refs(monkeypatch):
    """Quality gate should fail when filtered citations are empty."""

    monkeypatch.setattr(settings, "REPORT_MIN_COMPLETED_BLOCKS", 1, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_PARAGRAPHS_TOTAL", 4, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_DISTINCT_CITATIONS", 2, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_CITATION_PARAGRAPH_COVERAGE", 0.2, raising=False)

    with pytest.raises(RuntimeError, match="high_quality_refs=0 < 2"):
        ResearchPipeline._validate_report_quality(
            report_quality={
                "paragraphs_total": 6,
                "citations_mentions": 0,
                "citations_distinct_count": 0,
                "citation_paragraph_coverage": 0.0,
            },
            allowed_refs=[],
            completed_blocks=1,
        )


def test_report_quality_gate_warns_but_passes_on_residual_placeholder_citations(
    monkeypatch, caplog
):
    """[N] placeholder markers are stripped before quality gate runs.

    After stripping, any residual placeholder count is logged as a WARNING but
    does NOT raise RuntimeError — the report should still be accepted as long as
    real clickable citations are present and other thresholds are met.
    """

    import logging

    monkeypatch.setattr(settings, "REPORT_MIN_COMPLETED_BLOCKS", 1, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_PARAGRAPHS_TOTAL", 4, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_DISTINCT_CITATIONS", 2, raising=False)
    monkeypatch.setattr(settings, "REPORT_MIN_CITATION_PARAGRAPH_COVERAGE", 0.2, raising=False)

    # Should NOT raise — report has real citations (mentions=4, distinct=2) even though
    # placeholder_citation_markers is non-zero (leftover from analysis run before strip).
    with caplog.at_level(logging.WARNING):
        ResearchPipeline._validate_report_quality(
            report_quality={
                "paragraphs_total": 6,
                "citations_mentions": 4,
                "citations_distinct_count": 2,
                "citation_paragraph_coverage": 0.4,
                "placeholder_citation_markers": 3,
            },
            allowed_refs=[1, 2],
            completed_blocks=1,
        )
    assert any("placeholder" in r.message.lower() for r in caplog.records), (
        "Expected a warning log about placeholder markers"
    )
