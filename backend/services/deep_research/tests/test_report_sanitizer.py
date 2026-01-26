"""Tests for report citation sanitizer."""

from service.report_sanitizer import sanitize_citations


def test_sanitize_citations_removes_unknown_refs() -> None:
    """Ensure unknown references are removed."""

    report = "Finding A [1] and Finding B [2]."
    cleaned = sanitize_citations(report, allowed_refs=[1])
    assert "[[1]](#ref-1)" in cleaned
    assert "[2]" not in cleaned
    assert "[[2]]" not in cleaned


def test_sanitize_handles_anchor_format() -> None:
    """Ensure anchor-style citations are preserved when allowed."""

    report = "See [[3]](#ref-3) for details."
    cleaned = sanitize_citations(report, allowed_refs=[3])
    assert "[[3]](#ref-3)" in cleaned
