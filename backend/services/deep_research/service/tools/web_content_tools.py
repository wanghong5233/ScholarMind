"""Page-level web tools for open/read workflows."""

from __future__ import annotations

from typing import Any, Dict, List

from service.data_structures import ToolTrace, ToolType
from service.tools.base_tool import BaseTool, ToolContext, ToolResult
from service.web_search_client import WebSearchClient


class WebOpenPageTool(BaseTool):
    """Tool for opening and extracting a single web page."""

    def __init__(self, search_client: WebSearchClient, max_chars: int = 6000) -> None:
        """Initialize the open-page tool."""

        super().__init__(
            name="web.open_page",
            description="Open a URL and extract readable page content.",
            tool_type=ToolType.SEARCH,
            parameters_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 500, "maximum": 20000},
                },
                "required": ["url"],
            },
        )
        self._search_client = search_client
        self._max_chars = max(1000, int(max_chars))

    async def execute(self, context: ToolContext, parameters: Dict[str, Any]) -> ToolResult:
        """Execute web page open/read."""

        url = str(parameters.get("url") or "").strip()
        if not url:
            return ToolResult(
                success=False,
                summary="Missing url for web.open_page.",
                raw={},
                citations=[],
                trace=None,
                error="missing_url",
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

        max_chars = int(parameters.get("max_chars") or self._max_chars)
        page_payload = await self._search_client.open_page(url=url, max_chars=max_chars)
        title = str(page_payload.get("title") or "Untitled")
        content = str(page_payload.get("content") or "")
        summary = self._summarize_page(title=title, url=page_payload.get("url") or url, content=content)
        trace = self._build_trace(context=context, query=url, summary=summary)
        return ToolResult(
            success=True,
            summary=summary,
            raw=page_payload,
            citations=[],
            trace=trace,
        )

    @staticmethod
    def _summarize_page(*, title: str, url: str, content: str) -> str:
        """Build compact summary for opened page."""

        preview = (content or "")[:360]
        line = f"- {title}"
        if url:
            line += f" ({url})"
        if preview:
            line += f": {preview}"
        return line

    @staticmethod
    def _build_trace(
        *,
        context: ToolContext,
        query: str,
        summary: str,
    ) -> ToolTrace:
        """Build tool trace for open-page action."""

        trace = ToolTrace(
            tool_id=f"web.open_page:{context.block.block_id}:{query[:48]}",
            citation_id="NO-CIT",
            tool_type=ToolType.SEARCH,
            query=query,
            raw_answer=summary,
            summary=summary[:400],
        )
        trace.truncate_raw_answer()
        return trace


class WebFindInPageTool(BaseTool):
    """Tool for finding query-relevant snippets inside a page."""

    def __init__(self, search_client: WebSearchClient) -> None:
        """Initialize the find-in-page tool."""

        super().__init__(
            name="web.find_in_page",
            description="Find query-relevant snippets from a URL.",
            tool_type=ToolType.SEARCH,
            parameters_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "query": {"type": "string"},
                    "max_matches": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["url", "query"],
            },
        )
        self._search_client = search_client

    async def execute(self, context: ToolContext, parameters: Dict[str, Any]) -> ToolResult:
        """Execute in-page search."""

        url = str(parameters.get("url") or "").strip()
        query = str(parameters.get("query") or "").strip()
        max_matches = int(parameters.get("max_matches") or 5)
        if not url:
            return ToolResult(
                success=False,
                summary="Missing url for web.find_in_page.",
                raw={},
                citations=[],
                trace=None,
                error="missing_url",
            )
        if not query:
            return ToolResult(
                success=False,
                summary="Missing query for web.find_in_page.",
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

        payload = await self._search_client.find_in_page(
            url=url,
            query=query,
            max_matches=max_matches,
        )
        matches = payload.get("matches") or []
        summary = self._summarize_matches(url=url, query=query, matches=matches)
        trace = self._build_trace(context=context, query=f"{query} @ {url}", summary=summary)
        return ToolResult(
            success=True,
            summary=summary,
            raw=payload,
            citations=[],
            trace=trace,
        )

    @staticmethod
    def _summarize_matches(*, url: str, query: str, matches: List[str]) -> str:
        """Build compact summary from match snippets."""

        lines = [f"- Query: {query}", f"- URL: {url}"]
        if not matches:
            lines.append("- No direct snippet matched; page was still inspected.")
            return "\n".join(lines)
        for item in matches[:5]:
            lines.append(f"- {item}")
        return "\n".join(lines)

    @staticmethod
    def _build_trace(*, context: ToolContext, query: str, summary: str) -> ToolTrace:
        """Build trace for in-page query."""

        trace = ToolTrace(
            tool_id=f"web.find_in_page:{context.block.block_id}:{query[:32]}",
            citation_id="NO-CIT",
            tool_type=ToolType.SEARCH,
            query=query,
            raw_answer=summary,
            summary=summary[:400],
        )
        trace.truncate_raw_answer()
        return trace
