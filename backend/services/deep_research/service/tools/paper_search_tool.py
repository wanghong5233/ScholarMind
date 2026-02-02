"""Paper search tool for academic retrieval."""

from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import arxiv
except ImportError:  # pragma: no cover - optional dependency
    arxiv = None

from service.citation_manager import AsyncCitationManagerWrapper
from service.citation_utils import register_paper_citations
from service.data_structures import ScholarCitation, ToolTrace, ToolType
from service.rag_client import RAGClient
from service.tools.base_tool import BaseTool, ToolContext, ToolResult


class PaperSearchTool(BaseTool):
    """Tool that searches academic papers via Core API."""

    def __init__(
        self,
        rag_client: RAGClient,
        citation_manager: AsyncCitationManagerWrapper,
        max_results: int,
        *,
        default_providers: Optional[List[str]] = None,
        default_rank_by: str = "hybrid",
        min_per_provider: int = 1,
        arxiv_max_results: int = 8,
        arxiv_years_limit: Optional[int] = 5,
        arxiv_timeout_seconds: int = 20,
        arxiv_retries: int = 2,
        arxiv_delay_seconds: float = 0.5,
    ) -> None:
        """Initialize the paper search tool."""

        super().__init__(
            name="paper.search",
            description=(
                "Search academic papers (Semantic Scholar + optional arXiv) and return metadata plus abstracts."
            ),
            tool_type=ToolType.SEARCH,
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "year": {"type": "string"},
                    "kb_id": {"type": "integer", "minimum": 1},
                    "providers": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["semantic_scholar", "arxiv"]},
                    },
                    "rank_by": {
                        "type": "string",
                        "enum": ["hybrid", "recent", "citations", "relevance"],
                    },
                },
                "required": ["query"],
            },
        )
        self._rag_client = rag_client
        self._citation_manager = citation_manager
        self._max_results = max(1, max_results)
        self._default_providers = self._normalize_providers(default_providers) or [
            "semantic_scholar"
        ]
        self._default_rank_by = (default_rank_by or "hybrid").lower()
        self._min_per_provider = max(0, int(min_per_provider))
        self._arxiv_max_results = max(1, int(arxiv_max_results))
        self._arxiv_years_limit = arxiv_years_limit if arxiv_years_limit is None else max(
            0, int(arxiv_years_limit)
        )
        self._arxiv_timeout_seconds = max(1, int(arxiv_timeout_seconds))
        self._arxiv_retries = max(0, int(arxiv_retries))
        self._arxiv_delay_seconds = max(0.0, float(arxiv_delay_seconds))
        self._arxiv_client = (
            arxiv.Client(
                page_size=self._arxiv_max_results,
                delay_seconds=self._arxiv_delay_seconds,
                num_retries=self._arxiv_retries,
            )
            if arxiv
            else None
        )

    async def execute(self, context: ToolContext, parameters: Dict[str, Any]) -> ToolResult:
        """Execute the paper search."""

        query = (parameters.get("query") or context.block.question or "").strip()
        if not query:
            return ToolResult(
                success=False,
                summary="Missing query for paper search.",
                raw={},
                citations=[],
                trace=None,
                error="missing_query",
            )

        kb_id = parameters.get("kb_id")
        if kb_id is None:
            if not context.session_id:
                return ToolResult(
                    success=False,
                    summary="Missing session_id for paper search.",
                    raw={},
                    citations=[],
                    trace=None,
                    error="missing_session_id",
                )
            try:
                session_detail = await self._rag_client.get_session_detail(
                    session_id=context.session_id,
                    user_id=context.user_id,
                )
                kb_id = session_detail.get("kbId")
            except Exception as exc:  # noqa: BLE001 - surface error for logging
                return ToolResult(
                    success=False,
                    summary="Failed to resolve session knowledge base.",
                    raw={"error": str(exc)},
                    citations=[],
                    trace=None,
                    error="session_lookup_failed",
                )

        if not kb_id:
            return ToolResult(
                success=False,
                summary="Missing knowledge base binding for paper search.",
                raw={},
                citations=[],
                trace=None,
                error="missing_kb_id",
            )

        limit = int(parameters.get("limit") or self._max_results)
        limit = max(1, min(limit, 50))
        year_filter = (parameters.get("year") or "").strip()
        providers = self._normalize_providers(parameters.get("providers")) or list(
            self._default_providers
        )
        rank_by = (parameters.get("rank_by") or self._default_rank_by or "hybrid").lower()
        if rank_by not in {"hybrid", "recent", "citations", "relevance"}:
            rank_by = "hybrid"

        errors: Dict[str, str] = {}
        results: List[Dict[str, Any]] = []

        if "semantic_scholar" in providers:
            try:
                semantic_papers = await self._rag_client.search_online_papers(
                    kb_id=int(kb_id),
                    query=query,
                    user_id=context.user_id,
                    limit=max(limit, self._max_results),
                    year=year_filter,
                )
                results.extend(
                    self._normalize_semantic_scholar(semantic_papers, provider_rank_offset=0)
                )
            except Exception as exc:  # noqa: BLE001 - surface error for logging
                errors["semantic_scholar"] = str(exc)

        if "arxiv" in providers:
            if not self._arxiv_client:
                errors["arxiv"] = "arxiv library not available"
            else:
                try:
                    arxiv_results = await self._search_arxiv(query, year_filter, limit, rank_by)
                    results.extend(
                        self._normalize_arxiv(arxiv_results, provider_rank_offset=len(results))
                    )
                except Exception as exc:  # noqa: BLE001 - surface error for logging
                    errors["arxiv"] = str(exc)

        if not results:
            summary = "No paper search results."
            return ToolResult(
                success=False,
                summary=summary,
                raw={"providers": providers, "errors": errors, "papers": []},
                citations=[],
                trace=self._build_trace(context, query, summary, []),
                error="paper_search_failed",
            )

        deduped = self._dedupe_results(results)
        ranked = self._rank_results(deduped, rank_by=rank_by, providers=providers)
        selected = self._select_with_diversity(
            ranked,
            limit=limit,
            providers=providers,
            min_per_provider=self._min_per_provider,
        )

        cleaned_selected = self._strip_internal_fields(selected)
        citations = await register_paper_citations(
            papers=cleaned_selected,
            citation_manager=self._citation_manager,
            source_id=context.block.block_id,
            provider="mixed",
        )
        summary = self._summarize_results(cleaned_selected)
        trace = self._build_trace(context, query, summary, citations)
        raw = {
            "providers": providers,
            "errors": errors,
            "papers": cleaned_selected,
        }
        return ToolResult(
            success=True,
            summary=summary,
            raw=raw,
            citations=citations,
            trace=trace,
        )

    @staticmethod
    def _summarize_results(results: List[Dict[str, Any]]) -> str:
        """Build a compact summary from paper results."""

        if not results:
            return "No paper search results."
        lines = []
        for item in results[:5]:
            title = item.get("title") or "Untitled"
            year = item.get("publication_year") or item.get("year")
            venue = item.get("journal_or_conference") or ""
            url = item.get("source_url") or item.get("doi") or ""
            abstract = (item.get("abstract") or "").strip()
            provider = item.get("provider")
            citation_count = item.get("citation_count")
            line = f"- {title}"
            if year:
                line += f" ({year})"
            if venue:
                line += f" [{venue}]"
            if provider:
                line += f" <{provider}>"
            if citation_count is not None:
                line += f" [cited {citation_count}]"
            if url:
                line += f" ({url})"
            if abstract:
                line += f": {abstract[:240]}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _build_trace(
        context: ToolContext,
        query: str,
        summary: str,
        citations: List[ScholarCitation],
    ) -> ToolTrace:
        """Build a ToolTrace for paper search."""

        citation_id = citations[0].citation_id if citations else "NO-CIT"
        trace = ToolTrace(
            tool_id=f"paper.search:{context.block.block_id}",
            citation_id=citation_id,
            tool_type=ToolType.SEARCH,
            query=query,
            raw_answer=summary,
            summary=summary[:400],
        )
        trace.truncate_raw_answer()
        return trace

    @staticmethod
    def _normalize_providers(raw: Any) -> List[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            tokens = [item.strip().lower() for item in re.split(r"[,\s]+", raw) if item.strip()]
            return [p for p in tokens if p in {"semantic_scholar", "arxiv"}]
        if isinstance(raw, list):
            providers = []
            for item in raw:
                if not isinstance(item, str):
                    continue
                normalized = item.strip().lower()
                if normalized in {"semantic_scholar", "arxiv"}:
                    providers.append(normalized)
            return providers
        return []

    @staticmethod
    def _strip_internal_fields(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cleaned: List[Dict[str, Any]] = []
        for item in results:
            payload = dict(item)
            payload.pop("_provider_rank", None)
            payload.pop("_score", None)
            cleaned.append(payload)
        return cleaned

    @staticmethod
    def _normalize_semantic_scholar(
        results: List[Dict[str, Any]], *, provider_rank_offset: int
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for idx, item in enumerate(results or []):
            payload = dict(item)
            payload.setdefault("provider", "semantic_scholar")
            payload["_provider_rank"] = provider_rank_offset + idx
            normalized.append(payload)
        return normalized

    def _normalize_arxiv(
        self, results: List[Any], *, provider_rank_offset: int
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for idx, result in enumerate(results or []):
            published = getattr(result, "published", None)
            year = published.year if published else None
            arxiv_id = self._extract_arxiv_id(getattr(result, "entry_id", ""))
            authors = [author.name for author in getattr(result, "authors", [])]
            normalized.append(
                {
                    "title": (getattr(result, "title", "") or "").strip(),
                    "authors": authors,
                    "abstract": (getattr(result, "summary", "") or "").strip(),
                    "publication_year": year,
                    "journal_or_conference": "arXiv",
                    "doi": getattr(result, "doi", None),
                    "source_url": getattr(result, "entry_id", None),
                    "arxiv_id": arxiv_id,
                    "published": published.isoformat() if published else None,
                    "primary_category": getattr(result, "primary_category", None),
                    "categories": list(getattr(result, "categories", []) or []),
                    "provider": "arxiv",
                    "_provider_rank": provider_rank_offset + idx,
                }
            )
        return normalized

    async def _search_arxiv(
        self, query: str, year_filter: str, limit: int, rank_by: str
    ) -> List[Any]:
        if not self._arxiv_client:
            return []
        min_year, max_year = self._parse_year_filter(year_filter)
        if min_year is None and max_year is None and self._arxiv_years_limit is not None:
            current_year = datetime.utcnow().year
            min_year = max(0, current_year - self._arxiv_years_limit)
            max_year = current_year

        sort_by = arxiv.SortCriterion.Relevance
        if rank_by in {"recent", "hybrid"}:
            sort_by = arxiv.SortCriterion.SubmittedDate
        search = arxiv.Search(
            query=query,
            max_results=max(limit, self._arxiv_max_results) * 3,
            sort_by=sort_by,
            sort_order=arxiv.SortOrder.Descending,
        )

        def run_search() -> List[Any]:
            results = list(self._arxiv_client.results(search))
            if min_year is None and max_year is None:
                return results[: max(limit, self._arxiv_max_results)]
            filtered = []
            for result in results:
                published = getattr(result, "published", None)
                if not published:
                    continue
                year = published.year
                if min_year is not None and year < min_year:
                    continue
                if max_year is not None and year > max_year:
                    continue
                filtered.append(result)
                if len(filtered) >= max(limit, self._arxiv_max_results):
                    break
            return filtered

        return await asyncio.wait_for(
            asyncio.to_thread(run_search),
            timeout=self._arxiv_timeout_seconds,
        )

    @staticmethod
    def _parse_year_filter(value: str) -> Tuple[Optional[int], Optional[int]]:
        if not value:
            return None, None
        cleaned = value.strip().lower()
        match = re.match(r"since[_:\s]*(\d{4})", cleaned)
        if match:
            return int(match.group(1)), None
        match = re.match(r"(\d{4})\s*-\s*(\d{4})", cleaned)
        if match:
            return int(match.group(1)), int(match.group(2))
        match = re.match(r"(\d{4})\s*-\s*$", cleaned)
        if match:
            return int(match.group(1)), None
        match = re.match(r"^\d{4}$", cleaned)
        if match:
            year = int(cleaned)
            return year, year
        return None, None

    @staticmethod
    def _extract_arxiv_id(url: str) -> Optional[str]:
        if not url:
            return None
        match = re.search(r"arxiv\.org/(?:abs|pdf)/(\d+\.\d+)", url)
        if match:
            return match.group(1)
        return url.split("/")[-1].split("v")[0] if "/" in url else url

    def _dedupe_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()
        for item in results:
            key = self._paper_key(item)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _paper_key(item: Dict[str, Any]) -> str:
        for field in ("doi", "semantic_scholar_id", "arxiv_id", "source_url"):
            value = (item.get(field) or "").strip().lower()
            if value:
                return value
        title = (item.get("title") or "").strip().lower()
        title = re.sub(r"[^a-z0-9]+", " ", title).strip()
        return title or str(item)

    def _rank_results(
        self,
        results: List[Dict[str, Any]],
        *,
        rank_by: str,
        providers: List[str],
    ) -> List[Dict[str, Any]]:
        if rank_by == "relevance":
            return sorted(results, key=lambda x: x.get("_provider_rank", 0))

        current_year = datetime.utcnow().year
        for item in results:
            year = item.get("publication_year") or item.get("year")
            recency = 0.0
            if isinstance(year, int):
                age = max(0, current_year - year)
                recency = 1.0 / (1.0 + age)
            citations = item.get("citation_count") or 0
            citation_score = math.log1p(max(0, int(citations))) / math.log1p(500)
            quality = 0.1 if item.get("highLight") else 0.0
            provider_rank = item.get("_provider_rank", 0)
            provider_score = 1.0 / (1.0 + provider_rank)

            if rank_by == "recent":
                score = recency + 0.05 * provider_score
            elif rank_by == "citations":
                score = citation_score + 0.05 * provider_score
            else:
                score = 0.55 * recency + 0.35 * citation_score + 0.1 * provider_score + quality
            item["_score"] = score

        return sorted(results, key=lambda x: x.get("_score", 0.0), reverse=True)

    @staticmethod
    def _select_with_diversity(
        results: List[Dict[str, Any]],
        *,
        limit: int,
        providers: List[str],
        min_per_provider: int,
    ) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        if min_per_provider <= 0:
            return results[:limit]

        selected: List[Dict[str, Any]] = []
        provider_counts = {provider: 0 for provider in providers}
        for provider in providers:
            for item in results:
                if item.get("provider") != provider:
                    continue
                if item in selected:
                    continue
                selected.append(item)
                provider_counts[provider] += 1
                if provider_counts[provider] >= min_per_provider:
                    break

        for item in results:
            if item in selected:
                continue
            selected.append(item)
            if len(selected) >= limit:
                break
        return selected[:limit]
