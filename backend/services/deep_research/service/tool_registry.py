"""Tool registry initialization for DeepResearch."""

from __future__ import annotations

import logging
from typing import Optional

from service.citation_manager import AsyncCitationManagerWrapper
from service.citation_quality import (
    DEFAULT_BLOCKED_CONTENT_TERMS,
    DEFAULT_SENSITIVE_DOMAIN_PATTERNS,
    split_csv_list,
)
from service.code_exec_client import CodeExecClient
from service.llm_client import LLMClient
from service.rag_client import RAGClient
from service.tools import (
    CodeExecTool,
    CompareTool,
    PaperSearchTool,
    RagAskTool,
    ToolRegistry,
    WebFindInPageTool,
    WebOpenPageTool,
    WebSearchTool,
)
from service.web_search_client import WebSearchClient
from core.config import settings

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
    if not provider_list:
        raise RuntimeError("PAPER_SEARCH_PROVIDERS is empty; cannot initialize paper.search tool.")
    registry.register(
        PaperSearchTool(
            rag_client,
            citation_manager,
            max_results=paper_search_max_results,
            default_providers=provider_list,
            default_rank_by=settings.PAPER_SEARCH_RANK_BY,
            min_per_provider=settings.PAPER_SEARCH_MIN_PER_PROVIDER,
            arxiv_max_results=settings.PAPER_SEARCH_ARXIV_MAX_RESULTS,
            arxiv_years_limit=settings.PAPER_SEARCH_ARXIV_MAX_AGE_YEARS,
            arxiv_timeout_seconds=settings.PAPER_SEARCH_ARXIV_TIMEOUT_SECONDS,
            arxiv_retries=settings.PAPER_SEARCH_ARXIV_RETRIES,
            arxiv_delay_seconds=settings.PAPER_SEARCH_ARXIV_DELAY_SECONDS,
            query_rewrite_llm_client=rag_llm_client,
        )
    )
    if web_search_client and web_search_client.is_configured():
        include_domains = split_csv_list(getattr(settings, "WEB_SEARCH_INCLUDE_DOMAINS", ""))
        exclude_domains = split_csv_list(getattr(settings, "WEB_SEARCH_EXCLUDE_DOMAINS", ""))
        domain_allowlist = split_csv_list(getattr(settings, "WEB_SEARCH_DOMAIN_ALLOWLIST", ""))
        domain_denylist = split_csv_list(getattr(settings, "WEB_SEARCH_DOMAIN_DENYLIST", ""))
        if not domain_denylist:
            domain_denylist = list(DEFAULT_SENSITIVE_DOMAIN_PATTERNS)
        blocked_terms = split_csv_list(getattr(settings, "WEB_SEARCH_BLOCKED_TERMS", ""))
        if not blocked_terms:
            blocked_terms = list(DEFAULT_BLOCKED_CONTENT_TERMS)
        registry.register(
            WebSearchTool(
                web_search_client,
                citation_manager,
                web_search_max_results,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                domain_allowlist=domain_allowlist,
                domain_denylist=domain_denylist,
                blocked_terms=blocked_terms,
                min_quality_score=float(getattr(settings, "WEB_SEARCH_MIN_QUALITY_SCORE", 0.6) or 0.6),
                min_query_overlap=float(getattr(settings, "WEB_SEARCH_MIN_QUERY_OVERLAP", 0.08) or 0.08),
            )
        )
        registry.register(WebOpenPageTool(web_search_client))
        registry.register(WebFindInPageTool(web_search_client))
    elif web_search_client:
        raise RuntimeError("Web search client created but API key is missing.")
    if code_exec_client:
        registry.register(CodeExecTool(code_exec_client))

    logger.info("Initialized DeepResearch tool registry with %s tools", len(registry.list_tools()))
    return registry
