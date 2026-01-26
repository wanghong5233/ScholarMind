"""Web search tool for DeepResearch."""

from __future__ import annotations

from typing import Any, Dict, List

from service.citation_manager import AsyncCitationManagerWrapper
from service.citation_utils import register_web_citations
from service.data_structures import ScholarCitation, ToolTrace, ToolType
from service.tools.base_tool import BaseTool, ToolContext, ToolResult
from service.web_search_client import WebSearchClient


class WebSearchTool(BaseTool):
    """Tool that performs web search via external provider."""

    def __init__(
        self,
        search_client: WebSearchClient,
        citation_manager: AsyncCitationManagerWrapper,
        max_results: int,
    ) -> None:
        """Initialize the web search tool."""

        super().__init__(
            name="web.search",
            description="Search the web for supporting evidence and sources.",
            tool_type=ToolType.SEARCH,
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
        )
        self._search_client = search_client
        self._citation_manager = citation_manager
        self._max_results = max(1, max_results)

    async def execute(self, context: ToolContext, parameters: Dict[str, Any]) -> ToolResult:
        """Execute the web search."""

        query = parameters.get("query") or context.block.question
        if not query:
            return ToolResult(
                success=False,
                summary="Missing query for web search.",
                raw={},
                citations=[],
                trace=None,
                error="missing_query",
            )
        if not self._search_client.is_configured():
            return ToolResult(
                success=False,
                summary="Web search is not configured.",
                raw={},
                citations=[],
                trace=None,
                error="web_search_not_configured",
            )

        max_results = parameters.get("max_results") or self._max_results
        response = await self._search_client.search(query, max_results=max_results)
        results = response.get("results", [])
        provider = response.get("provider", "web")
        citations = await register_web_citations(
            results=results,
            citation_manager=self._citation_manager,
            source_id=context.block.block_id,
            provider=provider,
        )
        summary = self._summarize_results(results)
        trace = self._build_trace(context, query, summary, citations)
        return ToolResult(
            success=True,
            summary=summary,
            raw=response,
            citations=citations,
            trace=trace,
        )

    @staticmethod
    def _summarize_results(results: List[Dict[str, Any]]) -> str:
        """Build a compact summary from web results."""

        if not results:
            return "No web search results."
        lines = []
        for item in results[:5]:
            title = item.get("title") or "Untitled"
            url = item.get("url") or ""
            snippet = item.get("snippet") or ""
            line = f"- {title}"
            if url:
                line += f" ({url})"
            if snippet:
                line += f": {snippet}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _build_trace(
        context: ToolContext,
        query: str,
        summary: str,
        citations: List[ScholarCitation],
    ) -> ToolTrace:
        """Build a ToolTrace for web search."""

        citation_id = citations[0].citation_id if citations else "NO-CIT"
        trace = ToolTrace(
            tool_id=f"web.search:{context.block.block_id}",
            citation_id=citation_id,
            tool_type=ToolType.SEARCH,
            query=query,
            raw_answer=summary,
            summary=summary[:400],
        )
        trace.truncate_raw_answer()
        return trace
