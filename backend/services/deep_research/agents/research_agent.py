"""Research agent for grounding topic blocks with tool routing."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agents.decision_agent import DecisionAgent, ResearchDecision
from service.citation_quality import (
    looks_question_like_query,
    rewrite_query_to_keywords,
    tokenize_query_terms,
)
from service.data_structures import ScholarCitation, ToolTrace, TopicBlock
from service.tool_router import ToolCall, ToolContext, ToolRouter

logger = logging.getLogger(__name__)


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
    web_search_traces: List[ToolTrace] = field(default_factory=list)
    paper_search_summary: Optional[str] = None
    paper_search_citations: List[ScholarCitation] = field(default_factory=list)
    paper_search_trace: Optional[ToolTrace] = None
    paper_search_traces: List[ToolTrace] = field(default_factory=list)
    code_exec_outputs: List[str] = field(default_factory=list)
    code_exec_raw: List[Dict[str, Any]] = field(default_factory=list)
    code_exec_traces: List[ToolTrace] = field(default_factory=list)
    decision: Optional[ResearchDecision] = None
    decision_history: List[Dict[str, Any]] = field(default_factory=list)
    evidence_quality_score: int = 0


@dataclass
class RoundActionCandidate:
    """Scored round action candidate for beam-style execution."""

    action: str
    score: float
    max_calls: int
    rationale: str


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
        max_decision_rounds: int = 1,
        min_evidence_quality_score: int = 0,
        fail_fast_on_tool_error: bool = False,
        allow_followup_query_expansion: bool = False,
        action_beam_width: int = 3,
        enable_code_exec_auto: bool = True,
        academic_paper_first: bool = True,
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
            max_decision_rounds (int): Max adaptive decision rounds per block.
            min_evidence_quality_score (int): Minimum quality score for early stop.
            fail_fast_on_tool_error (bool): Whether tool failures should abort block execution.
            allow_followup_query_expansion (bool): Whether follow-up questions can be
                expanded into direct search queries.
            action_beam_width (int): How many scored action groups to execute per round.
            enable_code_exec_auto (bool): Auto-enable code.exec only when strongly indicated.
            academic_paper_first (bool): Prefer paper.search over web.search on academic tasks.
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
        self._max_decision_rounds = max(1, max_decision_rounds)
        self._min_evidence_quality_score = max(0, min(100, min_evidence_quality_score))
        self._fail_fast_on_tool_error = bool(fail_fast_on_tool_error)
        self._allow_followup_query_expansion = bool(allow_followup_query_expansion)
        self._action_beam_width = max(1, int(action_beam_width or 1))
        self._enable_code_exec_auto = bool(enable_code_exec_auto)
        self._academic_paper_first = bool(academic_paper_first)

    async def research_block(
        self,
        block: TopicBlock,
        session_id: Optional[str],
        user_id: int,
        top_k: Optional[int] = None,
        index_mode: Optional[str] = None,
        language: Optional[str] = None,
        context_text: Optional[str] = None,
        global_topic: Optional[str] = None,
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
            global_topic (Optional[str]): Root user topic for dynamic query anchoring.
            use_web_search (bool): Whether to run web search.
            use_paper_search (bool): Whether to run paper search.
            code_exec_snippets (Optional[List[str]]): Optional code snippets.

        Returns:
            ResearchResult: Grounded summary and citations.
        """

        if not session_id:
            raise RuntimeError("Missing session_id; unable to call ScholarMind RAG.")

        context = ToolContext(
            block=block,
            session_id=session_id,
            user_id=user_id,
            top_k=top_k,
            index_mode=index_mode,
            language=language,
        )

        summary = ""
        base_citations: List[ScholarCitation] = []
        main_raw: Dict[str, Any] = {}
        main_trace: Optional[ToolTrace] = None
        if not self._is_retrieval_disabled(index_mode):
            main_result = await self._tool_router.execute(
                ToolCall(name="rag.ask", parameters={"question": block.question}, purpose="main"),
                context,
            )
            if not main_result.success:
                raise RuntimeError(
                    f"RAG ask failed: {main_result.error or main_result.summary or 'unknown_error'}"
                )
            summary = main_result.summary
            base_citations = main_result.citations
            main_raw = main_result.raw
            main_trace = main_result.trace

        # Accumulators across adaptive decision rounds.
        web_summaries: List[str] = []
        web_citations: List[ScholarCitation] = []
        web_traces: List[ToolTrace] = []
        paper_summaries: List[str] = []
        paper_citations: List[ScholarCitation] = []
        paper_traces: List[ToolTrace] = []
        code_exec_outputs: List[str] = []
        code_exec_raw: List[Dict[str, Any]] = []
        code_exec_traces: List[ToolTrace] = []
        compare_answer: Optional[str] = None
        compare_citations: List[ScholarCitation] = []
        compare_raw: Optional[Dict[str, Any]] = None
        compare_trace: Optional[ToolTrace] = None
        compare_attempted = False
        followup_answers: List[str] = []
        followup_citations: List[ScholarCitation] = []
        followup_traces: List[ToolTrace] = []
        executed_followup_questions: List[str] = []
        suggested_followup_questions: List[str] = []
        decision_history: List[Dict[str, Any]] = []
        final_decision: Optional[ResearchDecision] = None
        previous_signature = ""

        cumulative_citations = self._dedupe_citations(list(base_citations))
        cumulative_traces = self._dedupe_traces([main_trace] if main_trace else [])
        decision_summary = self._compose_decision_summary(
            base_summary=summary,
            web_summaries=web_summaries,
            paper_summaries=paper_summaries,
            compare_answer=compare_answer,
            followup_answers=followup_answers,
            code_outputs=code_exec_outputs,
        )
        evidence_quality_score = self._estimate_evidence_quality(
            summary=decision_summary,
            citations_count=len(cumulative_citations),
            traces_count=len(cumulative_traces),
        )
        academic_mode = self._is_academic_task(block=block, context_text=context_text)
        anchor_terms = self._derive_dynamic_anchor_terms(
            block=block,
            context_text=context_text,
            global_topic=global_topic,
        )

        for round_index in range(1, self._decision_round_budget() + 1):
            quality_before = evidence_quality_score
            decision_context = self._compose_decision_context(
                context_text=context_text,
                round_index=round_index,
                max_rounds=self._decision_round_budget(),
                quality_score=evidence_quality_score,
                decision_history=decision_history,
            )
            decision = await self._decision_agent.decide(
                topic=block.title,
                summary=decision_summary,
                citations_count=len(cumulative_citations),
                language=language,
                context_text=decision_context,
            )
            final_decision = decision
            suggested_followup_questions.extend(decision.followup_questions)
            round_signature = self._decision_signature(decision)
            code_auto_enabled = self._should_auto_enable_code_exec(
                block=block,
                decision=decision,
                code_exec_snippets=code_exec_snippets,
            )
            effective_use_code_exec = bool(use_code_exec) or code_auto_enabled
            round_budgets = self._allocate_round_budgets(
                decision=decision,
                use_web_search=use_web_search,
                use_paper_search=use_paper_search,
                use_code_exec=effective_use_code_exec,
                has_code_snippets=bool(code_exec_snippets),
                compare_pending=(not compare_attempted and decision.should_compare),
                academic_mode=academic_mode,
            )
            action_candidates = self._score_round_action_candidates(
                decision=decision,
                round_budgets=round_budgets,
                academic_mode=academic_mode,
                evidence_quality_score=evidence_quality_score,
            )
            selected_actions = self._select_action_beam(action_candidates)

            round_has_progress = False
            for candidate in selected_actions:
                action = candidate.action
                if action == "paper":
                    paper_summary_round, paper_citations_round, _paper_trace_round, paper_traces_round = (
                        await self._maybe_paper_search(
                            context=context,
                            use_paper_search=use_paper_search,
                            decision=decision,
                            max_calls=candidate.max_calls,
                            anchor_terms=anchor_terms,
                        )
                    )
                    if paper_summary_round:
                        paper_summaries.append(paper_summary_round)
                        round_has_progress = True
                    if paper_citations_round:
                        paper_citations.extend(paper_citations_round)
                        round_has_progress = True
                    if paper_traces_round:
                        paper_traces.extend(paper_traces_round)
                        round_has_progress = True
                    continue

                if action == "web":
                    web_summary_round, web_citations_round, _web_trace_round, web_traces_round = (
                        await self._maybe_web_search(
                            context=context,
                            use_web_search=use_web_search,
                            decision=decision,
                            max_calls=candidate.max_calls,
                            anchor_terms=anchor_terms,
                        )
                    )
                    if web_summary_round:
                        web_summaries.append(web_summary_round)
                        round_has_progress = True
                    if web_citations_round:
                        web_citations.extend(web_citations_round)
                        round_has_progress = True
                    if web_traces_round:
                        web_traces.extend(web_traces_round)
                        round_has_progress = True
                    continue

                if action == "code":
                    code_outputs_round, code_raw_round, code_traces_round = await self._maybe_code_exec(
                        context=context,
                        decision=decision,
                        use_code_exec=effective_use_code_exec,
                        code_exec_snippets=code_exec_snippets,
                        max_calls=candidate.max_calls,
                    )
                    if code_outputs_round:
                        code_exec_outputs.extend(code_outputs_round)
                        round_has_progress = True
                    if code_raw_round:
                        code_exec_raw.extend(code_raw_round)
                    if code_traces_round:
                        code_exec_traces.extend(code_traces_round)
                        round_has_progress = True
                    continue

                if action == "compare":
                    if compare_attempted:
                        continue
                    compare_attempted = True
                    compare_answer_round, compare_citations_round, compare_raw_round, compare_trace_round = (
                        await self._maybe_compare(
                            context=context,
                            raw=main_raw,
                            decision=decision,
                            max_calls=candidate.max_calls,
                        )
                    )
                    if compare_answer_round:
                        compare_answer = compare_answer_round
                        round_has_progress = True
                    if compare_citations_round:
                        compare_citations.extend(compare_citations_round)
                        round_has_progress = True
                    if compare_raw_round is not None:
                        compare_raw = compare_raw_round
                    if compare_trace_round:
                        compare_trace = compare_trace_round
                        round_has_progress = True
                    continue

                if action == "followup":
                    followup_answers_round, followup_citations_round, followup_traces_round = (
                        await self._maybe_followups(
                            context=context,
                            decision=decision,
                            max_calls=candidate.max_calls,
                        )
                    )
                    if followup_answers_round:
                        followup_answers.extend(followup_answers_round)
                        round_has_progress = True
                        for idx, _answer in enumerate(followup_answers_round):
                            if idx < len(decision.followup_questions):
                                executed_followup_questions.append(decision.followup_questions[idx])
                            else:
                                executed_followup_questions.append("Follow-up question")
                    if followup_citations_round:
                        followup_citations.extend(followup_citations_round)
                        round_has_progress = True
                    if followup_traces_round:
                        followup_traces.extend(followup_traces_round)
                        round_has_progress = True

            cumulative_citations = self._dedupe_citations(
                list(base_citations)
                + web_citations
                + paper_citations
                + followup_citations
                + compare_citations
            )
            cumulative_traces = self._dedupe_traces(
                ([main_trace] if main_trace else [])
                + web_traces
                + paper_traces
                + code_exec_traces
                + followup_traces
                + ([compare_trace] if compare_trace else [])
            )
            decision_summary = self._compose_decision_summary(
                base_summary=summary,
                web_summaries=web_summaries,
                paper_summaries=paper_summaries,
                compare_answer=compare_answer,
                followup_answers=followup_answers,
                code_outputs=code_exec_outputs,
            )
            evidence_quality_score = self._estimate_evidence_quality(
                summary=decision_summary,
                citations_count=len(cumulative_citations),
                traces_count=len(cumulative_traces),
            )
            decision_history.append(
                self._build_decision_record(
                    decision=decision,
                    round_index=round_index,
                    quality_before=quality_before,
                    quality_after=evidence_quality_score,
                    round_has_progress=round_has_progress,
                    round_budgets=round_budgets,
                    selected_actions=[candidate.__dict__ for candidate in selected_actions],
                )
            )

            quality_ok = evidence_quality_score >= self._min_evidence_quality_score
            if decision.sufficient and quality_ok:
                break
            if not round_has_progress:
                if round_signature == previous_signature:
                    break
                if not self._has_actionable_plan(
                    decision=decision,
                    use_web_search=use_web_search,
                    use_paper_search=use_paper_search,
                    use_code_exec=effective_use_code_exec,
                    has_code_snippets=bool(code_exec_snippets),
                    compare_pending=(not compare_attempted),
                ):
                    break
            previous_signature = round_signature

        if final_decision is None:
            final_decision = await self._decision_agent.decide(
                topic=block.title,
                summary=decision_summary,
                citations_count=len(cumulative_citations),
                language=language,
                context_text=context_text,
            )

        merged_web_summary = self._merge_summary_blocks(web_summaries)
        merged_paper_summary = self._merge_summary_blocks(paper_summaries)
        final_followup_questions = (
            executed_followup_questions
            if followup_answers
            else self._dedupe_texts(suggested_followup_questions)
        )
        return ResearchResult(
            summary=decision_summary or summary,
            citations=cumulative_citations,
            raw=main_raw,
            main_trace=main_trace,
            followup_questions=final_followup_questions,
            followup_answers=followup_answers,
            followup_citations=self._dedupe_citations(followup_citations),
            followup_traces=self._dedupe_traces(followup_traces),
            compare_answer=compare_answer,
            compare_citations=self._dedupe_citations(compare_citations),
            compare_raw=compare_raw,
            compare_trace=compare_trace,
            web_search_summary=merged_web_summary,
            web_search_citations=self._dedupe_citations(web_citations),
            web_search_trace=(web_traces[-1] if web_traces else None),
            web_search_traces=self._dedupe_traces(web_traces),
            paper_search_summary=merged_paper_summary,
            paper_search_citations=self._dedupe_citations(paper_citations),
            paper_search_trace=(paper_traces[-1] if paper_traces else None),
            paper_search_traces=self._dedupe_traces(paper_traces),
            code_exec_outputs=code_exec_outputs,
            code_exec_raw=code_exec_raw,
            code_exec_traces=self._dedupe_traces(code_exec_traces),
            decision=final_decision,
            decision_history=decision_history,
            evidence_quality_score=evidence_quality_score,
        )

    @staticmethod
    def _is_retrieval_disabled(index_mode: Optional[str]) -> bool:
        """Return True when request explicitly disables KB retrieval."""

        normalized = str(index_mode or "").strip().lower()
        return normalized in {"disabled", "off", "none", "false", "0"}

    def _handle_tool_failure(self, *, tool_name: str, detail: str) -> None:
        """Handle per-tool failure using configured fail-fast policy."""

        message = f"{tool_name} failed: {detail or 'unknown_error'}"
        if self._fail_fast_on_tool_error:
            raise RuntimeError(message)
        logger.warning("Tool call soft-failed and was skipped: %s", message)

    async def _maybe_web_search(
        self,
        context: ToolContext,
        use_web_search: bool,
        decision: ResearchDecision,
        max_calls: Optional[int] = None,
        anchor_terms: Optional[List[str]] = None,
    ) -> tuple[Optional[str], List[ScholarCitation], Optional[ToolTrace], List[ToolTrace]]:
        """Run optional web search tools."""

        if not (self._enable_web_search and use_web_search):
            return None, [], None, []
        if max_calls is not None and max_calls <= 0:
            return None, [], None, []

        web_calls = self._filter_tool_calls(decision.tool_calls, tool_name="web.search")
        decision_open_calls = self._filter_tool_calls(decision.tool_calls, tool_name="web.open_page")
        decision_find_calls = self._filter_tool_calls(decision.tool_calls, tool_name="web.find_in_page")
        expanded_calls = self._build_search_calls(
            tool_name="web.search",
            decision_calls=web_calls,
            followup_questions=decision.followup_questions,
            allow_extra=not decision.sufficient,
            expand_followups=self._allow_followup_query_expansion,
            anchor_terms=anchor_terms,
        )
        if not expanded_calls and not decision.sufficient:
            fallback_query = self._build_default_search_query(
                context.block,
                anchor_terms=anchor_terms,
            )
            if fallback_query:
                expanded_calls = [
                    ToolCall(
                        name="web.search",
                        parameters={"query": fallback_query},
                        purpose="fallback",
                    )
                ]

        summaries: List[tuple[str, str]] = []
        citations: List[ScholarCitation] = []
        last_trace: Optional[ToolTrace] = None
        traces: List[ToolTrace] = []
        search_budget = self._search_call_budget()
        if max_calls is not None:
            search_budget = min(search_budget, max_calls)
        if search_budget <= 0:
            return None, [], None, []
        enrichment_budget = self._web_enrichment_call_budget()
        if max_calls is not None:
            enrichment_budget = min(enrichment_budget, max(0, max_calls - search_budget))
        enrichment_used = 0
        attempted_calls = 0

        for call in expanded_calls[:search_budget]:
            if max_calls is not None and attempted_calls >= max_calls:
                break
            result = await self._tool_router.execute(call, context)
            attempted_calls += 1
            if result.trace:
                last_trace = result.trace
                traces.append(result.trace)
            if not result.success:
                self._handle_tool_failure(
                    tool_name="web.search",
                    detail=result.error or result.summary or "unknown_error",
                )
                continue
            query_text = str(call.parameters.get("query") or "").strip()
            if result.summary:
                summaries.append((query_text, result.summary))
            citations.extend(result.citations or [])
            if enrichment_used >= enrichment_budget:
                continue
            enrichment_calls = self._build_web_enrichment_calls(
                raw=result.raw,
                query=query_text or context.block.question,
            )
            for enrichment_call in enrichment_calls:
                if enrichment_used >= enrichment_budget:
                    break
                if max_calls is not None and attempted_calls >= max_calls:
                    break
                enrichment_result = await self._tool_router.execute(enrichment_call, context)
                enrichment_used += 1
                attempted_calls += 1
                if enrichment_result.trace:
                    last_trace = enrichment_result.trace
                    traces.append(enrichment_result.trace)
                if not enrichment_result.success:
                    self._handle_tool_failure(
                        tool_name=enrichment_call.name,
                        detail=enrichment_result.error
                        or enrichment_result.summary
                        or "unknown_error",
                    )
                    continue
                enrichment_query = str(
                    enrichment_call.parameters.get("query")
                    or enrichment_call.parameters.get("url")
                    or ""
                ).strip()
                if enrichment_result.summary:
                    summaries.append((enrichment_query, enrichment_result.summary))
                citations.extend(enrichment_result.citations or [])

        explicit_budget = max(0, enrichment_budget - enrichment_used)
        if max_calls is not None:
            explicit_budget = min(explicit_budget, max(0, max_calls - attempted_calls))
        for explicit_call in (decision_open_calls + decision_find_calls)[:explicit_budget]:
            if max_calls is not None and attempted_calls >= max_calls:
                break
            explicit_result = await self._tool_router.execute(explicit_call, context)
            attempted_calls += 1
            if explicit_result.trace:
                last_trace = explicit_result.trace
                traces.append(explicit_result.trace)
            if not explicit_result.success:
                self._handle_tool_failure(
                    tool_name=explicit_call.name,
                    detail=explicit_result.error or explicit_result.summary or "unknown_error",
                )
                continue
            explicit_query = str(
                explicit_call.parameters.get("query")
                or explicit_call.parameters.get("url")
                or ""
            ).strip()
            if explicit_result.summary:
                summaries.append((explicit_query, explicit_result.summary))
            citations.extend(explicit_result.citations or [])

        if not summaries and not citations:
            return None, [], last_trace, self._dedupe_traces(traces)
        return (
            self._merge_tool_summaries(summaries),
            self._dedupe_citations(citations),
            last_trace,
            self._dedupe_traces(traces),
        )

    async def _maybe_paper_search(
        self,
        context: ToolContext,
        use_paper_search: bool,
        decision: ResearchDecision,
        max_calls: Optional[int] = None,
        anchor_terms: Optional[List[str]] = None,
    ) -> tuple[Optional[str], List[ScholarCitation], Optional[ToolTrace], List[ToolTrace]]:
        """Run optional academic paper search tool."""

        if not use_paper_search:
            return None, [], None, []
        if max_calls is not None and max_calls <= 0:
            return None, [], None, []

        paper_calls = self._filter_tool_calls(decision.tool_calls, tool_name="paper.search")
        expanded_calls = self._build_search_calls(
            tool_name="paper.search",
            decision_calls=paper_calls,
            followup_questions=decision.followup_questions,
            allow_extra=not decision.sufficient,
            expand_followups=self._allow_followup_query_expansion,
            anchor_terms=anchor_terms,
        )
        if not expanded_calls and not decision.sufficient:
            fallback_query = self._build_default_search_query(
                context.block,
                anchor_terms=anchor_terms,
            )
            if fallback_query:
                expanded_calls = [
                    ToolCall(
                        name="paper.search",
                        parameters={"query": fallback_query},
                        purpose="fallback",
                    )
                ]

        summaries: List[tuple[str, str]] = []
        citations: List[ScholarCitation] = []
        last_trace: Optional[ToolTrace] = None
        traces: List[ToolTrace] = []
        search_budget = self._search_call_budget()
        if max_calls is not None:
            search_budget = min(search_budget, max_calls)
        for call in expanded_calls[:search_budget]:
            result = await self._tool_router.execute(call, context)
            if result.trace:
                last_trace = result.trace
                traces.append(result.trace)
            if not result.success:
                self._handle_tool_failure(
                    tool_name="paper.search",
                    detail=result.error or result.summary or "unknown_error",
                )
                continue
            query_text = str(call.parameters.get("query") or "").strip()
            if result.summary:
                summaries.append((query_text, result.summary))
            citations.extend(result.citations or [])

        if not summaries and not citations:
            return None, [], last_trace, self._dedupe_traces(traces)
        return (
            self._merge_tool_summaries(summaries),
            self._dedupe_citations(citations),
            last_trace,
            self._dedupe_traces(traces),
        )

    async def _maybe_code_exec(
        self,
        context: ToolContext,
        decision: ResearchDecision,
        use_code_exec: bool,
        code_exec_snippets: Optional[List[str]],
        max_calls: Optional[int] = None,
    ) -> tuple[List[str], List[Dict[str, Any]], List[ToolTrace]]:
        """Run optional code execution snippets."""

        outputs: List[str] = []
        raws: List[Dict[str, Any]] = []
        traces: List[ToolTrace] = []

        if not (self._enable_code_exec and use_code_exec):
            return outputs, raws, traces
        if max_calls is not None and max_calls <= 0:
            return outputs, raws, traces

        calls: List[ToolCall] = []
        for snippet in (code_exec_snippets or [])[: self._max_code_exec_snippets]:
            calls.append(ToolCall(name="code.exec", parameters={"code": snippet}, purpose="code"))

        calls.extend(self._filter_tool_calls(decision.tool_calls, tool_name="code.exec"))

        call_budget = self._max_tool_calls
        if max_calls is not None:
            call_budget = min(call_budget, max_calls)
        for call in calls[:call_budget]:
            result = await self._tool_router.execute(call, context)
            if not result.success:
                self._handle_tool_failure(
                    tool_name="code.exec",
                    detail=result.error or result.summary or "unknown_error",
                )
                continue
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
        max_calls: Optional[int] = None,
    ) -> tuple[Optional[str], List[ScholarCitation], Optional[Dict[str, Any]], Optional[ToolTrace]]:
        """Optionally call compare based on decision and citations."""

        if max_calls is not None and max_calls <= 0:
            return None, [], None, None
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
            self._handle_tool_failure(
                tool_name="rag.compare",
                detail=result.error or result.summary or "unknown_error",
            )
            return None, [], None, None
        return result.summary, result.citations, result.raw, result.trace

    async def _maybe_followups(
        self,
        context: ToolContext,
        decision: ResearchDecision,
        max_calls: Optional[int] = None,
    ) -> tuple[List[str], List[ScholarCitation], List[ToolTrace]]:
        """Optionally execute follow-up questions inline."""

        if self._followup_mode != "inline":
            return [], [], []
        if not decision.followup_questions:
            return [], [], []
        if max_calls is not None and max_calls <= 0:
            return [], [], []

        answers: List[str] = []
        citations: List[ScholarCitation] = []
        traces: List[ToolTrace] = []
        followup_budget = self._max_followup_queries
        if max_calls is not None:
            followup_budget = min(followup_budget, max_calls)
        for question in decision.followup_questions[:followup_budget]:
            result = await self._tool_router.execute(
                ToolCall(name="rag.ask", parameters={"question": question}, purpose="followup"),
                context,
            )
            if not result.success:
                self._handle_tool_failure(
                    tool_name="followup rag.ask",
                    detail=result.error or result.summary or "unknown_error",
                )
                continue
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

    def _decision_round_budget(self) -> int:
        """Return adaptive decision rounds for a block."""

        return max(1, self._max_decision_rounds)

    def _compose_decision_summary(
        self,
        *,
        base_summary: str,
        web_summaries: List[str],
        paper_summaries: List[str],
        compare_answer: Optional[str],
        followup_answers: List[str],
        code_outputs: List[str],
    ) -> str:
        """Compose a condensed evidence summary for next-round decisions."""

        sections: List[str] = []
        if str(base_summary or "").strip():
            sections.append(str(base_summary).strip())
        if web_summaries:
            web_text = self._merge_summary_blocks(web_summaries)
            if web_text:
                sections.append("Web evidence:\n" + web_text.strip())
        if paper_summaries:
            paper_text = self._merge_summary_blocks(paper_summaries)
            if paper_text:
                sections.append("Paper evidence:\n" + paper_text.strip())
        if compare_answer:
            sections.append("Compare notes:\n" + str(compare_answer).strip())
        if followup_answers:
            followup_text = self._merge_summary_blocks(followup_answers)
            if followup_text:
                sections.append("Follow-up notes:\n" + followup_text.strip())
        if code_outputs:
            code_text = self._merge_summary_blocks(code_outputs)
            if code_text:
                sections.append("Code outputs:\n" + code_text.strip())
        merged = "\n\n".join(section for section in sections if section).strip()
        return merged[:8000]

    def _compose_decision_context(
        self,
        *,
        context_text: Optional[str],
        round_index: int,
        max_rounds: int,
        quality_score: int,
        decision_history: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Compose per-round context to help the decision LLM reflect."""

        parts: List[str] = []
        if context_text:
            parts.append(str(context_text).strip())
        parts.append(
            (
                f"[Runtime] decision_round={round_index}/{max_rounds}; "
                f"evidence_quality={quality_score}/100; "
                f"quality_target={self._min_evidence_quality_score}/100."
            )
        )
        if decision_history:
            last = decision_history[-1]
            last_tools = ",".join(
                str(call.get("name") or "")
                for call in (last.get("tool_calls") or [])
                if call.get("name")
            )
            parts.append(
                (
                    f"[LastDecision] sufficient={last.get('sufficient')}; "
                    f"quality_after={last.get('quality_after')}; "
                    f"tools={last_tools or 'none'}; "
                    f"rationale={str(last.get('rationale') or '')[:500]}"
                )
            )
        merged = "\n\n".join(part for part in parts if part).strip()
        if not merged:
            return None
        return merged[:6000]

    @staticmethod
    def _estimate_evidence_quality(*, summary: str, citations_count: int, traces_count: int) -> int:
        """Estimate evidence quality with lightweight scoring."""

        summary_chars = len(str(summary or "").strip())
        summary_component = min(1.0, summary_chars / 900.0) * 40.0
        citation_component = min(1.0, max(0, citations_count) / 8.0) * 35.0
        trace_component = min(1.0, max(0, traces_count) / 8.0) * 25.0
        return int(round(summary_component + citation_component + trace_component))

    @staticmethod
    def _build_decision_record(
        *,
        decision: ResearchDecision,
        round_index: int,
        quality_before: int,
        quality_after: int,
        round_has_progress: bool,
        round_budgets: Dict[str, int],
        selected_actions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Build a persisted decision record with round metadata."""

        payload = decision.to_dict()
        payload["round"] = round_index
        payload["quality_before"] = quality_before
        payload["quality_after"] = quality_after
        payload["round_progress"] = round_has_progress
        payload["round_budgets"] = dict(round_budgets)
        payload["selected_actions"] = list(selected_actions or [])
        return payload

    def _has_actionable_plan(
        self,
        *,
        decision: ResearchDecision,
        use_web_search: bool,
        use_paper_search: bool,
        use_code_exec: bool,
        has_code_snippets: bool,
        compare_pending: bool,
    ) -> bool:
        """Check whether a decision still has meaningful next actions."""

        if decision.tool_calls:
            return True
        if self._followup_mode == "inline" and decision.followup_questions:
            return True
        if compare_pending and decision.should_compare:
            return True
        if (
            not decision.sufficient
            and (
                (use_web_search and self._enable_web_search)
                or use_paper_search
                or (use_code_exec and self._enable_code_exec and has_code_snippets)
            )
        ):
            return True
        return False

    @staticmethod
    def _decision_signature(decision: ResearchDecision) -> str:
        """Create a deterministic signature for loop-stagnation detection."""

        tool_parts: List[str] = []
        for call in decision.tool_calls or []:
            name = str(call.get("name") or "").strip().lower()
            params = call.get("parameters") if isinstance(call.get("parameters"), dict) else {}
            query = str(
                params.get("query") or params.get("question") or params.get("url") or ""
            ).strip().lower()
            tool_parts.append(f"{name}:{query}")
        followups = [str(item or "").strip().lower() for item in decision.followup_questions or []]
        return (
            f"s={int(bool(decision.sufficient))};"
            f"c={int(bool(decision.should_compare))};"
            f"t={'|'.join(sorted(tool_parts))};"
            f"f={'|'.join(sorted(followups))}"
        )

    @staticmethod
    def _is_tool_call_present(tool_calls: List[Dict[str, Any]], names: set[str]) -> bool:
        """Check if any tool call belongs to a target name set."""

        normalized = {str(name).strip().lower() for name in names}
        for call in tool_calls or []:
            if str(call.get("name") or "").strip().lower() in normalized:
                return True
        return False

    def _allocate_round_budgets(
        self,
        *,
        decision: ResearchDecision,
        use_web_search: bool,
        use_paper_search: bool,
        use_code_exec: bool,
        has_code_snippets: bool,
        compare_pending: bool,
        academic_mode: bool,
    ) -> Dict[str, int]:
        """Allocate per-round call budget across tool groups."""

        round_budget = max(1, self._max_tool_calls // max(1, self._decision_round_budget()))
        caps = {"web": 0, "paper": 0, "code": 0, "compare": 0, "followup": 0}
        if use_web_search and self._enable_web_search:
            has_web_intent = (
                not decision.sufficient
                or self._is_tool_call_present(
                    decision.tool_calls,
                    {"web.search", "web.open_page", "web.find_in_page"},
                )
            )
            if has_web_intent:
                if academic_mode and self._academic_paper_first:
                    caps["web"] = max(
                        1,
                        min(2, self._search_call_budget() + self._web_enrichment_call_budget()),
                    )
                else:
                    caps["web"] = max(
                        1,
                        min(4, self._search_call_budget() + self._web_enrichment_call_budget()),
                    )
        if use_paper_search:
            has_paper_intent = (
                not decision.sufficient
                or self._is_tool_call_present(decision.tool_calls, {"paper.search"})
            )
            if has_paper_intent:
                if academic_mode and self._academic_paper_first:
                    caps["paper"] = max(2, min(4, self._search_call_budget() + 1))
                else:
                    caps["paper"] = max(1, self._search_call_budget())
        if use_code_exec and self._enable_code_exec:
            has_code_intent = has_code_snippets or self._is_tool_call_present(decision.tool_calls, {"code.exec"})
            if has_code_intent:
                caps["code"] = max(1, min(self._max_code_exec_snippets or 1, self._max_tool_calls))
        if compare_pending and decision.should_compare:
            caps["compare"] = 1
        if self._followup_mode == "inline" and decision.followup_questions:
            caps["followup"] = max(1, min(self._max_followup_queries, len(decision.followup_questions)))

        allocated = {key: 0 for key in caps}
        active = [key for key, cap in caps.items() if cap > 0]
        if not active:
            return allocated

        budget_left = round_budget
        for key in active:
            if budget_left <= 0:
                break
            allocated[key] = 1
            budget_left -= 1

        if academic_mode and self._academic_paper_first:
            priority = ["paper", "web", "followup", "compare", "code"]
        else:
            priority = ["web", "paper", "followup", "compare", "code"]
        while budget_left > 0:
            advanced = False
            for key in priority:
                if allocated[key] >= caps[key]:
                    continue
                allocated[key] += 1
                budget_left -= 1
                advanced = True
                if budget_left <= 0:
                    break
            if not advanced:
                break
        return allocated

    @staticmethod
    def _is_academic_task(*, block: TopicBlock, context_text: Optional[str]) -> bool:
        """Detect whether a block is likely an academic/research task."""

        text = " ".join(
            item
            for item in [
                str(block.title or "").strip().lower(),
                str(block.question or "").strip().lower(),
                str(context_text or "").strip().lower(),
            ]
            if item
        )
        if not text:
            return False
        markers = (
            "paper",
            "survey",
            "benchmark",
            "dataset",
            "citation",
            "journal",
            "conference",
            "arxiv",
            "ieee",
            "acm",
            "tmc",
            "论文",
            "综述",
            "顶刊",
            "引用",
            "学术",
            "基准",
            "数据集",
            "研究生",
        )
        return any(marker in text for marker in markers)

    @classmethod
    def _looks_numeric_or_simulation_task(cls, *, block: TopicBlock, decision: ResearchDecision) -> bool:
        """Detect whether code execution is likely helpful for this task."""

        text = " ".join(
            [
                str(block.title or "").strip().lower(),
                str(block.question or "").strip().lower(),
                str(decision.rationale or "").strip().lower(),
            ]
        )
        markers = (
            "simulate",
            "simulation",
            "numerical",
            "calculate",
            "optimization",
            "runtime",
            "latency",
            "throughput",
            "ablation",
            "statistical",
            "数值",
            "仿真",
            "模拟",
            "计算",
            "优化",
            "时延",
            "吞吐",
            "消融",
            "对比实验",
            "python",
        )
        return any(marker in text for marker in markers)

    def _should_auto_enable_code_exec(
        self,
        *,
        block: TopicBlock,
        decision: ResearchDecision,
        code_exec_snippets: Optional[List[str]],
    ) -> bool:
        """Auto-enable code execution only when there is strong need."""

        if not (self._enable_code_exec and self._enable_code_exec_auto):
            return False
        if code_exec_snippets:
            return True
        if self._is_tool_call_present(decision.tool_calls, {"code.exec"}):
            return True
        return self._looks_numeric_or_simulation_task(block=block, decision=decision)

    def _score_round_action_candidates(
        self,
        *,
        decision: ResearchDecision,
        round_budgets: Dict[str, int],
        academic_mode: bool,
        evidence_quality_score: int,
    ) -> List[RoundActionCandidate]:
        """Score action groups and produce execution candidates for beam selection."""

        tool_presence = {
            "web": self._is_tool_call_present(
                decision.tool_calls, {"web.search", "web.open_page", "web.find_in_page"}
            ),
            "paper": self._is_tool_call_present(decision.tool_calls, {"paper.search"}),
            "code": self._is_tool_call_present(decision.tool_calls, {"code.exec"}),
            "compare": bool(decision.should_compare),
            "followup": bool(decision.followup_questions),
        }
        base_scores = {
            "paper": 86.0 if (academic_mode and self._academic_paper_first) else 72.0,
            "web": 64.0 if (academic_mode and self._academic_paper_first) else 80.0,
            "code": 46.0,
            "compare": 52.0,
            "followup": 56.0,
        }
        tie_break = {"paper": 5, "web": 4, "followup": 3, "compare": 2, "code": 1}
        candidates: List[RoundActionCandidate] = []
        for action, budget in (round_budgets or {}).items():
            max_calls = int(budget or 0)
            if max_calls <= 0:
                continue
            score = float(base_scores.get(action, 40.0))
            reasons: List[str] = []
            if tool_presence.get(action):
                score += 18.0
                reasons.append("decision_requested")
            if not decision.sufficient and action in {"paper", "web", "followup"}:
                score += 10.0
                reasons.append("insufficient_evidence")
            if (
                evidence_quality_score < self._min_evidence_quality_score
                and action in {"paper", "web", "followup"}
            ):
                score += 6.0
                reasons.append("quality_below_target")
            if action == "web" and academic_mode and self._academic_paper_first:
                score -= 6.0
                reasons.append("academic_web_deprioritized")
            score += float(tie_break.get(action, 0)) * 0.01
            candidates.append(
                RoundActionCandidate(
                    action=action,
                    score=score,
                    max_calls=max_calls,
                    rationale=";".join(reasons) or "base",
                )
            )
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates

    def _select_action_beam(self, candidates: List[RoundActionCandidate]) -> List[RoundActionCandidate]:
        """Select top-K action candidates for this round."""

        if not candidates:
            return []
        beam_width = max(1, min(self._action_beam_width, len(candidates)))
        return candidates[:beam_width]

    @staticmethod
    def _merge_summary_blocks(items: List[str]) -> Optional[str]:
        """Merge summary blocks while preserving order."""

        merged = "\n\n".join(str(item).strip() for item in items if str(item).strip()).strip()
        return merged or None

    @staticmethod
    def _dedupe_texts(items: List[str]) -> List[str]:
        """Deduplicate text list while preserving order."""

        seen: set[str] = set()
        deduped: List[str] = []
        for item in items or []:
            text = str(item or "").strip()
            if not text:
                continue
            key = " ".join(text.lower().split())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(text)
        return deduped

    def _build_search_calls(
        self,
        *,
        tool_name: str,
        decision_calls: List[ToolCall],
        followup_questions: List[str],
        allow_extra: bool,
        expand_followups: bool = True,
        anchor_terms: Optional[List[str]] = None,
    ) -> List[ToolCall]:
        """Build search calls from decision output plus follow-up expansion."""

        calls: List[ToolCall] = []
        seen_queries: set[str] = set()

        def append_call(query: Any, purpose: str, base_parameters: Optional[Dict[str, Any]] = None) -> None:
            sanitized_query = self._sanitize_search_query(
                query=query,
                anchor_terms=anchor_terms,
            )
            query_key = " ".join(sanitized_query.lower().split())
            if not query_key or query_key in seen_queries:
                return
            seen_queries.add(query_key)
            parameters = dict(base_parameters or {})
            parameters["query"] = sanitized_query
            calls.append(ToolCall(name=tool_name, parameters=parameters, purpose=purpose))

        for call in decision_calls:
            params = call.parameters if isinstance(call.parameters, dict) else {}
            append_call(
                query=params.get("query") or params.get("question"),
                purpose=call.purpose or "decision",
                base_parameters=params,
            )

        if allow_extra and expand_followups:
            for question in followup_questions[:2]:
                append_call(
                    query=question,
                    purpose="followup",
                    base_parameters={"query": question},
                )
        return calls

    def _search_call_budget(self) -> int:
        """Return a bounded search-call budget per block."""

        return max(1, min(3, self._max_tool_calls))

    def _web_enrichment_call_budget(self) -> int:
        """Return budget for page-level web enrichment calls."""

        base = max(0, self._max_tool_calls - self._search_call_budget())
        return max(0, min(6, base))

    # Matches any run of CJK Unified Ideographs (including extensions).
    _CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3000-\u303f]+")

    @classmethod
    def _strip_cjk_for_search(cls, text: str) -> str:
        """Remove CJK characters from a query string, leaving only ASCII/Latin terms.

        This is a safety-net for when the LLM emits Chinese characters inside a
        search query despite being instructed otherwise.  Academic databases such as
        Semantic Scholar and arXiv index almost exclusively English text, so CJK
        characters in a query term produce zero results.
        """
        stripped = cls._CJK_RUN_RE.sub(" ", text)
        return " ".join(stripped.split()).strip()

    @classmethod
    def _sanitize_search_query(cls, *, query: Any, anchor_terms: Optional[List[str]] = None) -> str:
        """Normalize a search query and reject placeholders.

        CJK-stripping is applied as a safety net so that Chinese user prompts do
        not inadvertently produce Chinese search queries when targeting English
        academic databases.
        """

        text = str(query or "").strip().replace("\n", " ")
        if not text:
            return ""
        if cls._looks_generic_search_query(text):
            return ""

        # Strip CJK characters before further processing.  English anchor terms are
        # then appended by _anchor_search_query so the query still has enough signal.
        text = cls._strip_cjk_for_search(text)
        if not text:
            # Entire query was CJK; fall back to anchor terms only.
            anchors = cls._normalize_anchor_terms(anchor_terms)
            return " ".join(anchors[:6])[:240] if anchors else ""

        normalized = " ".join(text.split())
        if looks_question_like_query(normalized):
            normalized = rewrite_query_to_keywords(normalized)
        normalized = cls._anchor_search_query(normalized, anchor_terms=anchor_terms)
        if cls._looks_generic_search_query(normalized):
            return ""
        if looks_question_like_query(normalized):
            # Guardrail: reject unresolved clarification-like strings.
            normalized = rewrite_query_to_keywords(normalized, max_terms=8)
            normalized = cls._anchor_search_query(normalized, anchor_terms=anchor_terms)
        term_count = len(tokenize_query_terms(normalized, max_terms=10))
        if term_count == 0:
            return ""
        return normalized[:240]

    @classmethod
    def _anchor_search_query(cls, query: str, *, anchor_terms: Optional[List[str]]) -> str:
        """Anchor generic search queries to topic-specific runtime terms."""

        normalized = " ".join(str(query or "").split()).strip()
        if not normalized:
            return ""
        anchors = cls._normalize_anchor_terms(anchor_terms)
        if not anchors:
            return normalized

        query_terms = tokenize_query_terms(normalized, max_terms=20)
        if not query_terms:
            return normalized
        query_set = set(query_terms)
        anchor_hits = sum(1 for term in anchors if term in query_set)
        overlap = anchor_hits / max(1, min(len(anchors), len(query_set)))

        generic_intent_markers = {
            "benchmark",
            "benchmarks",
            "dataset",
            "datasets",
            "method",
            "methods",
            "limitation",
            "limitations",
            "future",
            "方向",
            "场景",
            "应用",
            "方法",
            "局限",
            "限制",
            "未来",
            "基准",
            "数据集",
            "论文",
            "证据",
        }
        has_generic_intent = any(term in generic_intent_markers for term in query_set)
        should_anchor = overlap < 0.35 or has_generic_intent or len(query_terms) <= 3
        if not should_anchor:
            return normalized

        missing = [term for term in anchors if term not in query_set]
        if not missing:
            return normalized
        anchored_query = f"{normalized} {' '.join(missing[:4])}".strip()
        return anchored_query[:240]

    @staticmethod
    def _normalize_anchor_terms(anchor_terms: Optional[List[str]], max_terms: int = 10) -> List[str]:
        """Normalize runtime anchor terms while preserving order."""

        terms: List[str] = []
        seen: set[str] = set()
        for raw in anchor_terms or []:
            for token in tokenize_query_terms(str(raw or ""), max_terms=max_terms * 2):
                if token in seen:
                    continue
                seen.add(token)
                terms.append(token)
                if len(terms) >= max(1, max_terms):
                    return terms
        return terms

    @classmethod
    def _derive_dynamic_anchor_terms(
        cls,
        *,
        block: TopicBlock,
        context_text: Optional[str],
        global_topic: Optional[str] = None,
    ) -> List[str]:
        """Extract dynamic search anchors from user topic + plan/block context.

        Only English tokens are returned as anchors so that CJK characters from
        Chinese user queries do not contaminate English academic search queries
        sent to Semantic Scholar / arXiv.
        """

        block_text = " ".join(
            item
            for item in [
                str(block.title or "").strip(),
                str(block.question or "").strip(),
            ]
            if item
        )
        block_terms = tokenize_query_terms(block_text, max_terms=16)
        topic_terms = tokenize_query_terms(str(global_topic or ""), max_terms=12)
        context_terms: List[str] = []
        if context_text:
            # Keep context lightweight to avoid polluting anchors with stale chat history.
            context_excerpt = str(context_text).strip()[:1600]
            context_terms = tokenize_query_terms(context_excerpt, max_terms=12)

        merged: List[str] = []
        seen: set[str] = set()
        # Only keep English tokens – CJK anchor terms are useless for English academic
        # databases and inflate the overlap denominator in citation filtering.
        _EN_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{2,}$")
        for term in block_terms + topic_terms + context_terms:
            if term in seen:
                continue
            if not _EN_TOKEN.match(term):
                continue
            seen.add(term)
            merged.append(term)
            if len(merged) >= 12:
                break
        return merged

    @staticmethod
    def _looks_generic_search_query(query: str) -> bool:
        """Detect generic placeholder queries that hurt retrieval quality."""

        normalized = " ".join(str(query or "").strip().lower().split())
        if not normalized:
            return True
        generic_markers = {
            "research topic",
            "study topic",
            "research question",
            "topic",
            "研究主题",
            "研究问题",
            "研究背景",
            "核心机制",
            "局限性",
            "未来方向",
            "应用场景",
            "数据集基准",
            "代表性论文",
            "主题",
            "背景定义",
            "局限与未来",
        }
        return normalized in generic_markers

    def _build_default_search_query(
        self,
        block: TopicBlock,
        anchor_terms: Optional[List[str]] = None,
    ) -> str:
        """Build a fallback query from block title/question when decision lacks one."""

        candidates = [
            str(block.title or "").strip(),
            str(block.question or "").strip(),
            f"{str(block.title or '').strip()} {str(block.question or '').strip()}".strip(),
        ]
        for candidate in candidates:
            sanitized = self._sanitize_search_query(query=candidate, anchor_terms=anchor_terms)
            if sanitized:
                return sanitized
        return ""

    @staticmethod
    def _merge_tool_summaries(items: List[tuple[str, str]]) -> Optional[str]:
        """Merge multiple tool summaries into a compact readable block."""

        if not items:
            return None
        if len(items) == 1:
            return items[0][1]
        lines: List[str] = []
        for query, summary in items:
            query_text = str(query or "").strip()
            if query_text:
                lines.append(f"Query: {query_text}")
            lines.append(summary)
        return "\n\n".join(lines)

    @staticmethod
    def _dedupe_citations(citations: List[ScholarCitation]) -> List[ScholarCitation]:
        """Deduplicate citations while preserving order."""

        seen: set[str] = set()
        deduped: List[ScholarCitation] = []
        for citation in citations or []:
            key = (
                str(citation.citation_id or "").strip()
                or f"{str(citation.title or '').strip()}|{str(citation.url or '').strip()}"
            )
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(citation)
        return deduped

    @staticmethod
    def _dedupe_traces(traces: List[ToolTrace]) -> List[ToolTrace]:
        """Deduplicate traces while preserving order."""

        seen: set[str] = set()
        deduped: List[ToolTrace] = []
        for trace in traces or []:
            key = str(trace.tool_id or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(trace)
        return deduped

    @staticmethod
    def _extract_urls_from_search_raw(raw: Dict[str, Any], limit: int = 2) -> List[str]:
        """Extract unique URLs from a web.search raw payload."""

        urls: List[str] = []
        seen: set[str] = set()
        for item in (raw or {}).get("results") or []:
            url = str(item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= max(1, limit):
                break
        return urls

    def _build_web_enrichment_calls(self, *, raw: Dict[str, Any], query: str) -> List[ToolCall]:
        """Build page-level calls after web.search results are returned."""

        calls: List[ToolCall] = []
        urls = self._extract_urls_from_search_raw(raw, limit=2)
        for url in urls:
            calls.append(
                ToolCall(
                    name="web.open_page",
                    parameters={"url": url, "max_chars": 6000},
                    purpose="web_open",
                )
            )
            calls.append(
                ToolCall(
                    name="web.find_in_page",
                    parameters={"url": url, "query": query, "max_matches": 3},
                    purpose="web_find",
                )
            )
        return calls

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
