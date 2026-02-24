"""Tests for report citation sanitizer."""

from service.report_sanitizer import sanitize_citations, sanitize_report_markdown_structure


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


def test_sanitize_report_markdown_structure_fixes_broken_ref_suffix() -> None:
    """Ensure duplicated/dangling reference suffixes are removed safely."""

    report = "Result keeps citation [[29]](#ref-29)(#ref-29"
    cleaned = sanitize_report_markdown_structure(report)
    assert cleaned == "Result keeps citation [[29]](#ref-29)"


def test_sanitize_report_markdown_structure_drops_truncated_table_rows() -> None:
    """Ensure malformed markdown table rows are removed from report body."""

    report = (
        "| col1 | col2 |\n"
        "| --- | --- |\n"
        "| good | row |\n"
        "| broken | row\n"
        "\n"
        "tail"
    )
    cleaned = sanitize_report_markdown_structure(report)
    assert "| good | row |" in cleaned
    assert "| broken | row" not in cleaned
    assert cleaned.endswith("tail")
