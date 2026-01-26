"""RAG ask tool for ScholarMind."""

from __future__ import annotations

from typing import Any, Dict, List

from service.citation_manager import AsyncCitationManagerWrapper
from service.citation_utils import register_rag_citations
from service.data_structures import ScholarCitation, ToolTrace, ToolType
from service.rag_client import RAGClient
from service.tools.base_tool import BaseTool, ToolContext, ToolResult


class RagAskTool(BaseTool):
    """Tool that queries ScholarMind RAG."""

    def __init__(
        self,
        rag_client: RAGClient,
        citation_manager: AsyncCitationManagerWrapper,
    ) -> None:
        """Initialize the RAG ask tool."""

        super().__init__(
            name="rag.ask",
            description="Query ScholarMind RAG for grounded answers.",
            tool_type=ToolType.RAG,
            parameters_schema={
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1},
                    "index_mode": {"type": "string"},
                },
                "required": ["question"],
            },
        )
        self._rag_client = rag_client
        self._citation_manager = citation_manager

    async def execute(self, context: ToolContext, parameters: Dict[str, Any]) -> ToolResult:
        """Execute the RAG ask call."""

        question = parameters.get("question") or context.block.question
        if not question or not context.session_id:
            return ToolResult(
                success=False,
                summary="Missing question or session_id for RAG ask.",
                raw={},
                citations=[],
                trace=None,
                error="missing_question_or_session_id",
            )

        top_k = parameters.get("top_k", context.top_k)
        index_mode = parameters.get("index_mode", context.index_mode)

        answer = await self._rag_client.ask(
            session_id=context.session_id,
            question=question,
            user_id=context.user_id,
            top_k=top_k,
            index_mode=index_mode,
        )
        citations = await register_rag_citations(
            rag_citations=answer.citations,
            citation_manager=self._citation_manager,
            source_id=context.block.block_id,
        )
        summary = answer.answer or "No answer returned from ScholarMind RAG."
        trace = self._build_trace(context, question, summary, citations)
        return ToolResult(
            success=True,
            summary=summary,
            raw={"answer": answer.answer, "citations": answer.citations, "chunks": answer.chunks},
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
        """Build a ToolTrace for a RAG call."""

        citation_id = citations[0].citation_id if citations else "NO-CIT"
        trace = ToolTrace(
            tool_id=f"rag.ask:{context.block.block_id}",
            citation_id=citation_id,
            tool_type=ToolType.RAG,
            query=question,
            raw_answer=summary,
            summary=summary[:400],
        )
        trace.truncate_raw_answer()
        return trace
