"""Research agent for grounding topic blocks with tool routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agents.decision_agent import DecisionAgent, ResearchDecision
from service.data_structures import ScholarCitation, ToolTrace, TopicBlock
from service.tool_router import ToolCall, ToolContext, ToolRouter


@dataclass
class ResearchResult:
    """Structured research output for a topic block."""

    summary: str
    citations: List[ScholarCitation]
    raw: Dict[str, Any]
    main_trace: Optional[ToolTrace] = None
    followup_questions: List[str] = field(default_factory=list)
    followup_answers: List[str] = field(default_factory=list)
    followup_citations: List[ScholarCitation] = field(default_factory=list)
    followup_traces: List[ToolTrace] = field(default_factory=list)
    compare_answer: Optional[str] = None
    compare_citations: List[ScholarCitation] = field(default_factory=list)
    compare_raw: Optional[Dict[str, Any]] = None
    compare_trace: Optional[ToolTrace] = None
    web_search_summary: Optional[str] = None
    web_search_citations: List[ScholarCitation] = field(default_factory=list)
    web_search_trace: Optional[ToolTrace] = None
    paper_search_summary: Optional[str] = None
    paper_search_citations: List[ScholarCitation] = field(default_factory=list)
    paper_search_trace: Optional[ToolTrace] = None
    code_exec_outputs: List[str] = field(default_factory=list)
    code_exec_raw: List[Dict[str, Any]] = field(default_factory=list)
    code_exec_traces: List[ToolTrace] = field(default_factory=list)
    decision: Optional[ResearchDecision] = None


class ResearchAgent:
    """Run tool calls and normalize research outputs."""

    def __init__(
        self,
        tool_router: ToolRouter,
        decision_agent: DecisionAgent,
        min_docs_for_compare: int,
        max_docs_for_compare: int,
        followup_mode: str,
        max_followup_queries: int,
        enable_web_search: bool,
        enable_code_exec: bool,
        max_code_exec_snippets: int,
        max_tool_calls: int,
    ) -> None:
        """Initialize the research agent.

        Args:
            tool_router (ToolRouter): Router for tool execution.
            decision_agent (DecisionAgent): Decision agent for tool selection.
            min_docs_for_compare (int): Minimum document count for compare.
            max_docs_for_compare (int): Maximum document count for compare.
            followup_mode (str): Follow-up execution mode.
            max_followup_queries (int): Max inline follow-up queries.
            enable_web_search (bool): Whether web search is enabled.
            enable_code_exec (bool): Whether code execution is enabled.
            max_code_exec_snippets (int): Max code exec snippets per block.
            max_tool_calls (int): Max tool calls per block (safety budget).
        """

        self._tool_router = tool_router
        self._decision_agent = decision_agent
        self._min_docs_for_compare = max(2, min_docs_for_compare)
        self._max_docs_for_compare = max(2, max_docs_for_compare)
        self._followup_mode = (followup_mode or "queue").lower()
        self._max_followup_queries = max(0, max_followup_queries)
        self._enable_web_search = enable_web_search
        self._enable_code_exec = enable_code_exec
        self._max_code_exec_snippets = max(0, max_code_exec_snippets)
        self._max_tool_calls = max(1, max_tool_calls)

    async def research_block(
        self,
        block: TopicBlock,
        session_id: Optional[str],
        user_id: int,
        top_k: Optional[int] = None,
        index_mode: Optional[str] = None,
        language: Optional[str] = None,
        context_text: Optional[str] = None,
        use_web_search: bool = False,
        use_paper_search: bool = False,
        use_code_exec: bool = False,
        code_exec_snippets: Optional[List[str]] = None,
    ) -> ResearchResult:
        """Ground a topic block with tool routing.

        Args:
            block (TopicBlock): Topic block to research.
            session_id (Optional[str]): ScholarMind session id.
            user_id (int): ScholarMind user id.
            top_k (Optional[int]): Retrieval top_k override.
            index_mode (Optional[str]): Retrieval index mode.
            language (Optional[str]): Language hint.
            context_text (Optional[str]): Optional conversation context.
            use_web_search (bool): Whether to run web search.
            use_paper_search (bool): Whether to run paper search.
            code_exec_snippets (Optional[List[str]]): Optional code snippets.

        Returns:
            ResearchResult: Grounded summary and citations.
        """

        if not session_id:
            return ResearchResult(
                summary="Missing session_id; unable to call ScholarMind RAG.",
                citations=[],
                raw={"error": "missing_session_id"},
            )

        context = ToolContext(
            block=block,
            session_id=session_id,
            user_id=user_id,
            top_k=top_k,
            index_mode=index_mode,
            language=language,
        )

        main_result = await self._tool_router.execute(
            ToolCall(name="rag.ask", parameters={"question": block.question}, purpose="main"),
            context,
        )
        if not main_result.success:
            return ResearchResult(
                summary=main_result.summary or "RAG ask failed.",
                citations=[],
                raw=main_result.raw,
                main_trace=main_result.trace,
            )

        summary = main_result.summary
        citations = main_result.citations
        decision = await self._decision_agent.decide(
            topic=block.title,
            summary=summary,
            citations_count=len(citations),
            language=language,
            context_text=context_text,
        )

        web_summary, web_citations, web_trace = await self._maybe_web_search(
            context=context,
            use_web_search=use_web_search,
            decision=decision,
        )

        paper_summary, paper_citations, paper_trace = await self._maybe_paper_search(
            context=context,
            use_paper_search=use_paper_search,
            decision=decision,
        )

        code_exec_outputs, code_exec_raw, code_exec_traces = await self._maybe_code_exec(
            context=context,
            decision=decision,
            use_code_exec=use_code_exec,
            code_exec_snippets=code_exec_snippets,
        )

        compare_answer, compare_citations, compare_raw, compare_trace = await self._maybe_compare(
            context=context,
            raw=main_result.raw,
            decision=decision,
        )

        followup_answers, followup_citations, followup_traces = await self._maybe_followups(
            context=context,
            decision=decision,
        )

        return ResearchResult(
            summary=summary,
            citations=citations,
            raw=main_result.raw,
            main_trace=main_result.trace,
            followup_questions=decision.followup_questions,
            followup_answers=followup_answers,
            followup_citations=followup_citations,
            followup_traces=followup_traces,
            compare_answer=compare_answer,
            compare_citations=compare_citations,
            compare_raw=compare_raw,
            compare_trace=compare_trace,
            web_search_summary=web_summary,
            web_search_citations=web_citations,
            web_search_trace=web_trace,
            paper_search_summary=paper_summary,
            paper_search_citations=paper_citations,
            paper_search_trace=paper_trace,
            code_exec_outputs=code_exec_outputs,
            code_exec_raw=code_exec_raw,
            code_exec_traces=code_exec_traces,
            decision=decision,
        )

    async def _maybe_web_search(
        self,
        context: ToolContext,
        use_web_search: bool,
        decision: ResearchDecision,
    ) -> tuple[Optional[str], List[ScholarCitation], Optional[ToolTrace]]:
        """Run optional web search tools."""

        if not (self._enable_web_search and use_web_search):
            return None, [], None

        web_calls = self._filter_tool_calls(decision.tool_calls, tool_name="web.search")
        if not web_calls:
            web_calls = [ToolCall(name="web.search", parameters={"query": context.block.question}, purpose="web")]
        call = web_calls[0]
        result = await self._tool_router.execute(call, context)
        if not result.success:
            return None, [], result.trace
        return result.summary, result.citations, result.trace

    async def _maybe_paper_search(
        self,
        context: ToolContext,
        use_paper_search: bool,
        decision: ResearchDecision,
    ) -> tuple[Optional[str], List[ScholarCitation], Optional[ToolTrace]]:
        """Run optional academic paper search tool."""

        if not use_paper_search:
            return None, [], None

        paper_calls = self._filter_tool_calls(decision.tool_calls, tool_name="paper.search")
        if not paper_calls:
            paper_calls = [
                ToolCall(name="paper.search", parameters={"query": context.block.question}, purpose="paper")
            ]
        call = paper_calls[0]
        result = await self._tool_router.execute(call, context)
        if not result.success:
            return None, [], result.trace
        return result.summary, result.citations, result.trace

    async def _maybe_code_exec(
        self,
        context: ToolContext,
        decision: ResearchDecision,
        use_code_exec: bool,
        code_exec_snippets: Optional[List[str]],
    ) -> tuple[List[str], List[Dict[str, Any]], List[ToolTrace]]:
        """Run optional code execution snippets."""

        outputs: List[str] = []
        raws: List[Dict[str, Any]] = []
        traces: List[ToolTrace] = []

        if not (self._enable_code_exec and use_code_exec):
            return outputs, raws, traces

        calls: List[ToolCall] = []
        for snippet in (code_exec_snippets or [])[: self._max_code_exec_snippets]:
            calls.append(ToolCall(name="code.exec", parameters={"code": snippet}, purpose="code"))

        calls.extend(self._filter_tool_calls(decision.tool_calls, tool_name="code.exec"))

        for call in calls[: self._max_tool_calls]:
            result = await self._tool_router.execute(call, context)
            if result.summary:
                outputs.append(result.summary)
            if result.raw:
                raws.append(result.raw)
            if result.trace:
                traces.append(result.trace)

        return outputs, raws, traces

    async def _maybe_compare(
        self,
        context: ToolContext,
        raw: Dict[str, Any],
        decision: ResearchDecision,
    ) -> tuple[Optional[str], List[ScholarCitation], Optional[Dict[str, Any]], Optional[ToolTrace]]:
        """Optionally call compare based on decision and citations."""

        if not decision.should_compare:
            return None, [], None, None

        citations = raw.get("citations", [])
        doc_ids = self._extract_doc_ids(citations)
        if len(doc_ids) < self._min_docs_for_compare:
            return None, [], None, None

        call = ToolCall(
            name="rag.compare",
            parameters={
                "doc_ids": doc_ids[: self._max_docs_for_compare],
                "dimensions": decision.compare_dimensions,
                "question": context.block.question,
            },
            purpose="compare",
        )
        result = await self._tool_router.execute(call, context)
        if not result.success:
            return None, [], result.raw, result.trace
        return result.summary, result.citations, result.raw, result.trace

    async def _maybe_followups(
        self,
        context: ToolContext,
        decision: ResearchDecision,
    ) -> tuple[List[str], List[ScholarCitation], List[ToolTrace]]:
        """Optionally execute follow-up questions inline."""

        if self._followup_mode != "inline":
            return [], [], []
        if not decision.followup_questions:
            return [], [], []

        answers: List[str] = []
        citations: List[ScholarCitation] = []
        traces: List[ToolTrace] = []
        for question in decision.followup_questions[: self._max_followup_queries]:
            result = await self._tool_router.execute(
                ToolCall(name="rag.ask", parameters={"question": question}, purpose="followup"),
                context,
            )
            if result.summary:
                answers.append(result.summary)
            citations.extend(result.citations)
            if result.trace:
                traces.append(result.trace)
        return answers, citations, traces

    @staticmethod
    def _extract_doc_ids(citations: List[Dict[str, Any]]) -> List[int]:
        """Extract document ids from ScholarMind citations."""

        doc_ids = []
        for item in citations or []:
            doc_id = item.get("document_id")
            try:
                doc_id_int = int(doc_id)
            except (TypeError, ValueError):
                continue
            doc_ids.append(doc_id_int)
        return sorted(set(doc_ids))

    def _filter_tool_calls(self, tool_calls: List[Dict[str, Any]], tool_name: str) -> List[ToolCall]:
        """Filter tool calls from decision output by name."""

        calls: List[ToolCall] = []
        for call in tool_calls or []:
            name = call.get("name")
            if name != tool_name:
                continue
            parameters = call.get("parameters") or {}
            calls.append(ToolCall(name=name, parameters=parameters, purpose="decision"))
        return calls
