"""Tests for CitationManager."""

from service.citation_manager import CitationManager
from service.data_structures import ScholarCitation


def test_generate_ids() -> None:
    """Verify plan and research citation id formats."""

    manager = CitationManager("research_test")
    assert manager.generate_plan_citation_id() == "PLAN-01"
    assert manager.generate_plan_citation_id() == "PLAN-02"
    assert manager.generate_research_citation_id("B001") == "CIT-B001-01"
    assert manager.generate_research_citation_id("B001") == "CIT-B001-02"


def test_reference_numbers() -> None:
    """Ensure reference numbering starts from 1 and stays stable."""

    manager = CitationManager("research_test")
    citation = ScholarCitation(
        citation_id="CIT-B001-01",
        title="Sample Paper",
        url="https://example.com",
        snippet="Sample snippet",
        source_type="rag",
    )
    ref_number = manager.add_citation(citation)
    assert ref_number == 1
    assert manager.get_ref_number("CIT-B001-01") == 1
