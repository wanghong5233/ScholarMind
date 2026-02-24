from __future__ import annotations

from service.core.ingestion.interfaces import ParsedBlock
from service.core.ingestion.structured_doc_builder import StructuredDocumentBuilder


def test_find_start_index_exact_match() -> None:
    builder = StructuredDocumentBuilder()
    blocks = [
        ParsedBlock(text="preface section", metadata={}),
        ParsedBlock(text="graph neural networks improve retrieval quality", metadata={}),
        ParsedBlock(text="appendix", metadata={}),
    ]
    idx = builder._find_start_index("graph neural networks improve retrieval quality", blocks)
    assert idx == 1


def test_find_start_index_fuzzy_match_with_ocr_noise() -> None:
    builder = StructuredDocumentBuilder()
    blocks = [
        ParsedBlock(text="introduction", metadata={}),
        ParsedBlock(
            text="grap neural netwroks improove retrievel qualtiy in experimnts",
            metadata={},
        ),
        ParsedBlock(text="results", metadata={}),
    ]
    idx = builder._find_start_index(
        "graph neural networks improve retrieval quality in experiments",
        blocks,
    )
    assert idx == 1


def test_collect_indices_stops_after_target_coverage() -> None:
    builder = StructuredDocumentBuilder()
    blocks = [
        ParsedBlock(text="alpha beta gamma", metadata={}),
        ParsedBlock(text="delta epsilon", metadata={}),
        ParsedBlock(text="completely unrelated tail", metadata={}),
    ]
    indices = builder._collect_indices(
        start_idx=0,
        normalized_target="alpha beta gamma delta epsilon",
        mineru_blocks=blocks,
    )
    assert indices == [0, 1]
