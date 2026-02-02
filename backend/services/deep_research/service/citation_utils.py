"""Utilities for registering citations from ScholarMind outputs."""

from typing import Any, Dict, List

from service.citation_manager import AsyncCitationManagerWrapper
from service.data_structures import ScholarCitation


async def register_rag_citations(
    rag_citations: List[Dict[str, Any]],
    citation_manager: AsyncCitationManagerWrapper,
    source_id: str,
) -> List[ScholarCitation]:
    """Normalize ScholarMind citations and register them with the citation manager.

    Args:
        rag_citations (List[Dict[str, Any]]): Raw citations from ScholarMind.
        citation_manager (AsyncCitationManagerWrapper): Citation registry wrapper.
        source_id (str): Source id used to generate citation ids.

    Returns:
        List[ScholarCitation]: Registered citations with reference numbers.
    """

    citations: List[ScholarCitation] = []
    seen_keys: set[str] = set()

    for item in rag_citations or []:
        key = str(item.get("id") or item.get("chunk_id") or item.get("document_id") or "")
        if key and key in seen_keys:
            continue
        if key:
            seen_keys.add(key)
        citation_id = await citation_manager.generate_research_citation_id(source_id)
        citation = ScholarCitation(
            citation_id=citation_id,
            title=item.get("document_title") or item.get("document_name"),
            url=item.get("url") or item.get("doi"),
            snippet=item.get("snippet") or item.get("source_text"),
            source_type=item.get("source") or item.get("parser_engine"),
            metadata=item,
        )
        ref_number = await citation_manager.add_citation(citation)
        citation.ref_number = ref_number
        citations.append(citation)

    return citations


async def register_web_citations(
    results: List[Dict[str, Any]],
    citation_manager: AsyncCitationManagerWrapper,
    source_id: str,
    provider: str,
) -> List[ScholarCitation]:
    """Normalize web search results and register them as citations.

    Args:
        results (List[Dict[str, Any]]): Normalized web search results.
        citation_manager (AsyncCitationManagerWrapper): Citation registry wrapper.
        source_id (str): Source id used to generate citation ids.
        provider (str): Search provider name.

    Returns:
        List[ScholarCitation]: Registered citations with reference numbers.
    """

    citations: List[ScholarCitation] = []
    seen_keys: set[str] = set()

    for item in results or []:
        key = str(item.get("url") or item.get("title") or "")
        if key and key in seen_keys:
            continue
        if key:
            seen_keys.add(key)
        citation_id = await citation_manager.generate_research_citation_id(source_id)
        citation = ScholarCitation(
            citation_id=citation_id,
            title=item.get("title"),
            url=item.get("url"),
            snippet=item.get("snippet"),
            source_type="web",
            metadata={"provider": provider, **item},
        )
        ref_number = await citation_manager.add_citation(citation)
        citation.ref_number = ref_number
        citations.append(citation)

    return citations


async def register_paper_citations(
    papers: List[Dict[str, Any]],
    citation_manager: AsyncCitationManagerWrapper,
    source_id: str,
    provider: str = "semantic_scholar",
) -> List[ScholarCitation]:
    """Normalize paper search results and register them as citations.

    Args:
        papers (List[Dict[str, Any]]): Paper metadata results.
        citation_manager (AsyncCitationManagerWrapper): Citation registry wrapper.
        source_id (str): Source id used to generate citation ids.
        provider (str): Paper search provider name.

    Returns:
        List[ScholarCitation]: Registered citations with reference numbers.
    """

    citations: List[ScholarCitation] = []
    seen_keys: set[str] = set()

    for item in papers or []:
        key = str(
            item.get("semantic_scholar_id")
            or item.get("doi")
            or item.get("source_url")
            or item.get("title")
            or ""
        )
        if key and key in seen_keys:
            continue
        if key:
            seen_keys.add(key)
        citation_id = await citation_manager.generate_research_citation_id(source_id)
        provider_name = str(item.get("provider") or provider)
        citation = ScholarCitation(
            citation_id=citation_id,
            title=item.get("title"),
            url=item.get("source_url") or item.get("doi"),
            snippet=item.get("abstract"),
            source_type="paper",
            metadata={"provider": provider_name, **item},
        )
        ref_number = await citation_manager.add_citation(citation)
        citation.ref_number = ref_number
        citations.append(citation)

    return citations
