"""Tool registry initialization for DeepResearch."""

from __future__ import annotations

import logging
from typing import Optional

from service.citation_manager import AsyncCitationManagerWrapper
from service.code_exec_client import CodeExecClient
from service.rag_client import RAGClient
from service.tools import CodeExecTool, CompareTool, RagAskTool, ToolRegistry, WebSearchTool
from service.web_search_client import WebSearchClient

logger = logging.getLogger(__name__)


def create_tool_registry(
    rag_client: RAGClient,
    citation_manager: AsyncCitationManagerWrapper,
    web_search_client: Optional[WebSearchClient],
    code_exec_client: Optional[CodeExecClient],
    web_search_max_results: int,
) -> ToolRegistry:
    """Create and initialize the DeepResearch tool registry."""

    registry = ToolRegistry()
    registry.register(RagAskTool(rag_client, citation_manager))
    registry.register(CompareTool(rag_client, citation_manager))
    if web_search_client:
        registry.register(WebSearchTool(web_search_client, citation_manager, web_search_max_results))
    if code_exec_client:
        registry.register(CodeExecTool(code_exec_client))

    logger.info("Initialized DeepResearch tool registry with %s tools", len(registry.list_tools()))
    return registry
