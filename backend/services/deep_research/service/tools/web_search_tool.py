"""Web search tool for DeepResearch."""

from __future__ import annotations

import logging
from urllib.parse import urlparse
from typing import Any, Dict, List

from service.citation_manager import AsyncCitationManagerWrapper
from service.citation_quality import ACADEMIC_DOMAIN_HINTS, assess_web_result_quality
from service.citation_utils import register_web_citations
from service.data_structures import ScholarCitation, ToolTrace, ToolType
from service.tools.base_tool import BaseTool, ToolContext, ToolResult
from service.web_search_client import WebSearchClient

logger = logging.getLogger(__name__)


class WebSearchTool(BaseTool):
    """Tool that performs web search via external provider."""

    def __init__(
        self,
        search_client: WebSearchClient,
        citation_manager: AsyncCitationManagerWrapper,
        max_results: int,
        *,
        include_domains: List[str],
        exclude_domains: List[str],
        domain_allowlist: List[str],
        domain_denylist: List[str],
        blocked_terms: List[str],
        min_quality_score: float,
        min_query_overlap: float,
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
        self._include_domains = [str(item or "").strip().lower() for item in include_domains if item]
        self._exclude_domains = [str(item or "").strip().lower() for item in exclude_domains if item]
        self._domain_allowlist = [
            str(item or "").strip().lower() for item in domain_allowlist if item
        ]
        self._domain_denylist = [str(item or "").strip().lower() for item in domain_denylist if item]
        self._blocked_terms = [str(item or "").strip().lower() for item in blocked_terms if item]
        self._min_quality_score = float(min_quality_score or 0.0)
        self._min_query_overlap = max(0.0, float(min_query_overlap or 0.0))

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
        academic_query = self._is_academic_query(str(query))
        include_domains = list(self._include_domains)
        if academic_query and not include_domains:
            include_domains = list(self._domain_allowlist) or list(ACADEMIC_DOMAIN_HINTS)
        response = await self._search_client.search(
            query,
            max_results=max_results,
            include_domains=include_domains,
            exclude_domains=self._exclude_domains,
        )
        results = response.get("results", [])
        ranked_results = self._rank_results_by_source_quality(
            results=results,
            query=query,
            max_results=max_results,
            domain_allowlist=self._domain_allowlist,
            domain_denylist=self._domain_denylist,
            blocked_terms=self._blocked_terms,
            min_quality_score=self._min_quality_score,
            min_query_overlap=self._min_query_overlap,
        )
        if not ranked_results:
            summary = "No trustworthy web results after quality filtering."
            trace = self._build_trace(context, query, summary, [])
            return ToolResult(
                success=True,
                summary=summary,
                raw={**response, "results": ranked_results},
                citations=[],
                trace=trace,
            )
        provider = response.get("provider", "web")
        citations = await register_web_citations(
            results=ranked_results,
            citation_manager=self._citation_manager,
            source_id=context.block.block_id,
            provider=provider,
        )
        summary = self._summarize_results(ranked_results)
        trace = self._build_trace(context, query, summary, citations)
        return ToolResult(
            success=True,
            summary=summary,
            raw={**response, "results": ranked_results},
            citations=citations,
            trace=trace,
        )

    @staticmethod
    def _rank_results_by_source_quality(
        *,
        results: List[Dict[str, Any]],
        query: str,
        max_results: int,
        domain_allowlist: List[str],
        domain_denylist: List[str],
        blocked_terms: List[str],
        min_quality_score: float,
        min_query_overlap: float,
    ) -> List[Dict[str, Any]]:
        """Rank web results by domain quality for research-heavy prompts."""

        if not results:
            return []
        academic_query = WebSearchTool._is_academic_query(query)
        effective_allowlist = list(domain_allowlist)
        if academic_query and not effective_allowlist:
            effective_allowlist = list(ACADEMIC_DOMAIN_HINTS)
        scored: List[Dict[str, Any]] = []
        dropped = 0
        for idx, item in enumerate(results):
            payload = dict(item)
            assessment = assess_web_result_quality(
                query=query,
                title=str(payload.get("title") or ""),
                snippet=str(payload.get("snippet") or ""),
                url=str(payload.get("url") or ""),
                academic_query=academic_query,
                allowlist=effective_allowlist,
                denylist=domain_denylist,
                blocked_terms=blocked_terms,
                min_score=min_quality_score,
                min_query_overlap=min_query_overlap,
            )
            if not assessment.accepted:
                dropped += 1
                continue
            score = assessment.score
            # Keep some provider ordering signal to avoid large jumps on similar quality sites.
            score += max(0.0, 1.0 - 0.08 * idx)
            payload["_source_domain"] = assessment.domain
            payload["_source_score"] = score
            scored.append(payload)
        if dropped:
            logger.info("web.search quality filter dropped %s candidate(s)", dropped)
        if not scored:
            return []
        scored.sort(key=lambda row: float(row.get("_source_score", 0.0)), reverse=True)
        trimmed = scored[: max(1, int(max_results or 1))]
        for item in trimmed:
            item.pop("_source_score", None)
        return trimmed

    @staticmethod
    def _is_academic_query(query: str) -> bool:
        normalized = str(query or "").lower()
        markers = (
            "paper",
            "survey",
            "citation",
            "benchmark",
            "journal",
            "conference",
            "arxiv",
            "ieee",
            "acm",
            "论文",
            "综述",
            "引用",
            "基准",
            "顶刊",
            "会议",
            "期刊",
        )
        return any(marker in normalized for marker in markers)

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
