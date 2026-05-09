from __future__ import annotations

from schemas.document import DocumentCreate
from service.document_identity import extract_arxiv_id, normalize_doi


def test_normalize_doi_from_url_and_prefix() -> None:
    assert normalize_doi("https://doi.org/10.1234/ABC-Def") == "10.1234/abc-def"
    assert normalize_doi("DOI:10.5555/XYZ.1") == "10.5555/xyz.1"


def test_extract_arxiv_id_strips_version() -> None:
    assert extract_arxiv_id("https://arxiv.org/abs/2310.06825v2") == "2310.06825"
    assert extract_arxiv_id("arXiv:2310.06825v7") == "2310.06825"
    assert extract_arxiv_id("https://arxiv.org/pdf/1706.03762v5.pdf") == "1706.03762"


def test_document_create_normalizes_identifiers() -> None:
    doc = DocumentCreate(
        title="test",
        ingestion_source="online_import",
        doi="https://doi.org/10.1000/ABC",
        semantic_scholar_id="https://www.semanticscholar.org/paper/title/ABCDEF123456",
        source_url="HTTPS://ArXiv.org/abs/2310.06825v2",
    )
    assert doc.doi == "10.1000/abc"
    assert doc.semantic_scholar_id == "abcdef123456"
    assert doc.source_url == "https://arxiv.org/abs/2310.06825v2"
