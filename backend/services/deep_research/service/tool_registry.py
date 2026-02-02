"""Tool registry initialization for DeepResearch."""

from __future__ import annotations

import logging
from typing import Optional

from service.citation_manager import AsyncCitationManagerWrapper
from service.code_exec_client import CodeExecClient
from service.llm_client import LLMClient
from service.rag_client import RAGClient
from service.tools import (
    CodeExecTool,
    CompareTool,
    PaperSearchTool,
    RagAskTool,
    ToolRegistry,
    WebSearchTool,
)
from service.web_search_client import WebSearchClient
from config import settings

logger = logging.getLogger(__name__)


def create_tool_registry(
    rag_client: RAGClient,
    citation_manager: AsyncCitationManagerWrapper,
    web_search_client: Optional[WebSearchClient],
    code_exec_client: Optional[CodeExecClient],
    web_search_max_results: int,
    paper_search_max_results: int,
    rag_llm_client: Optional[LLMClient] = None,
) -> ToolRegistry:
    """Create and initialize the DeepResearch tool registry."""

    registry = ToolRegistry()
    registry.register(RagAskTool(rag_client, citation_manager, llm_client=rag_llm_client))
    registry.register(CompareTool(rag_client, citation_manager))
    provider_list = [
        item.strip().lower()
        for item in (settings.PAPER_SEARCH_PROVIDERS or "").split(",")
        if item.strip()
    ]
    registry.register(
        PaperSearchTool(
            rag_client,
            citation_manager,
            max_results=paper_search_max_results,
            default_providers=provider_list or ["semantic_scholar"],
            default_rank_by=settings.PAPER_SEARCH_RANK_BY,
            min_per_provider=settings.PAPER_SEARCH_MIN_PER_PROVIDER,
            arxiv_max_results=settings.PAPER_SEARCH_ARXIV_MAX_RESULTS,
            arxiv_years_limit=settings.PAPER_SEARCH_ARXIV_MAX_AGE_YEARS,
            arxiv_timeout_seconds=settings.PAPER_SEARCH_ARXIV_TIMEOUT_SECONDS,
            arxiv_retries=settings.PAPER_SEARCH_ARXIV_RETRIES,
            arxiv_delay_seconds=settings.PAPER_SEARCH_ARXIV_DELAY_SECONDS,
        )
    )
    if web_search_client:
        registry.register(WebSearchTool(web_search_client, citation_manager, web_search_max_results))
    if code_exec_client:
        registry.register(CodeExecTool(code_exec_client))

    logger.info("Initialized DeepResearch tool registry with %s tools", len(registry.list_tools()))
    return registry
