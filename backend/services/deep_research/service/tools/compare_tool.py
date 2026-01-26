"""Compare tool for cross-document analysis."""

from __future__ import annotations

from typing import Any, Dict, List

from service.citation_manager import AsyncCitationManagerWrapper
from service.citation_utils import register_rag_citations
from service.data_structures import ScholarCitation, ToolTrace, ToolType
from service.rag_client import RAGClient
from service.tools.base_tool import BaseTool, ToolContext, ToolResult


class CompareTool(BaseTool):
    """Tool that calls ScholarMind compare."""

    def __init__(
        self,
        rag_client: RAGClient,
        citation_manager: AsyncCitationManagerWrapper,
    ) -> None:
        """Initialize the compare tool."""

        super().__init__(
            name="rag.compare",
            description="Compare multiple documents using ScholarMind /compare.",
            tool_type=ToolType.COMPARE,
            parameters_schema={
                "type": "object",
                "properties": {
                    "doc_ids": {"type": "array", "items": {"type": "integer"}},
                    "dimensions": {"type": "array", "items": {"type": "string"}},
                    "question": {"type": "string"},
                },
                "required": ["doc_ids"],
            },
        )
        self._rag_client = rag_client
        self._citation_manager = citation_manager

    async def execute(self, context: ToolContext, parameters: Dict[str, Any]) -> ToolResult:
        """Execute the compare call."""

        if not context.session_id:
            return ToolResult(
                success=False,
                summary="Missing session_id for compare.",
                raw={},
                citations=[],
                trace=None,
                error="missing_session_id",
            )
        doc_ids = parameters.get("doc_ids") or []
        if not doc_ids or len(doc_ids) < 2:
            return ToolResult(
                success=False,
                summary="Insufficient documents for compare.",
                raw={},
                citations=[],
                trace=None,
                error="insufficient_doc_ids",
            )

        dimensions = parameters.get("dimensions") or []
        payload = {"docIds": doc_ids, "dimensions": dimensions}
        response = await self._rag_client.compare(
            session_id=context.session_id,
            payload=payload,
            user_id=context.user_id,
        )
        answer = response.get("answer", "")
        citations = await register_rag_citations(
            rag_citations=response.get("citations", []),
            citation_manager=self._citation_manager,
            source_id=context.block.block_id,
        )
        trace = self._build_trace(context, parameters.get("question") or context.block.question, answer, citations)
        return ToolResult(
            success=True,
            summary=answer,
            raw=response,
            citations=citations,
            trace=trace,
        )

    @staticmethod
    def _build_trace(
        context: ToolContext,
        question: str,
        summary: str,
        citations: List[ScholarCitation],
    ) -> ToolTrace:
        """Build a ToolTrace for compare."""

        citation_id = citations[0].citation_id if citations else "NO-CIT"
        trace = ToolTrace(
            tool_id=f"rag.compare:{context.block.block_id}",
            citation_id=citation_id,
            tool_type=ToolType.COMPARE,
            query=question,
            raw_answer=summary,
            summary=summary[:400],
        )
        trace.truncate_raw_answer()
        return trace
