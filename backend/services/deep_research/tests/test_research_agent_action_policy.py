"""Tests for action scoring, beam execution, and code auto-enable policies."""

from __future__ import annotations

import pytest

from agents.decision_agent import ResearchDecision
from agents.research_agent import ResearchAgent
from service.data_structures import ScholarCitation, ToolTrace, ToolType, TopicBlock
from service.tool_router import ToolExecutionResult


def _trace(tool_id: str, query: str, summary: str) -> ToolTrace:
    """Build a minimal trace payload."""

    return ToolTrace(
        tool_id=tool_id,
        citation_id="CIT-TRACE",
        tool_type=ToolType.SEARCH,
        query=query,
        raw_answer=summary,
        summary=summary[:200],
    )


class _DecisionStub:
    """Deterministic decision agent stub."""

    def __init__(self, decision: ResearchDecision) -> None:
        self._decision = decision

    async def decide(self, *_args, **_kwargs):  # type: ignore[override]
        return self._decision


class _RecordingRouter:
    """Router stub that records execution order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, call, _context):  # type: ignore[override]
        query = str(
            call.parameters.get("query")
            or call.parameters.get("question")
            or call.parameters.get("url")
            or call.parameters.get("code")
            or ""
        ).strip()
        self.calls.append(f"{call.name}:{query}")
        if call.name == "rag.ask":
            return ToolExecutionResult(
                tool_name="rag.ask",
                purpose=call.purpose,
                success=True,
                summary="base summary",
                raw={"citations": []},
                citations=[],
                trace=_trace("rag.ask:main", query or "main", "base summary"),
            )
        if call.name == "paper.search":
            return ToolExecutionResult(
                tool_name="paper.search",
                purpose=call.purpose,
                success=True,
                summary=f"paper result for {query}",
                raw={},
                citations=[
                    ScholarCitation(
                        citation_id="CIT-PAPER-1",
                        title="Paper",
                        url="https://arxiv.org/abs/2401.00001",
                        snippet="edge scheduling",
                        source_type="paper",
                    )
                ],
                trace=_trace("paper.search:1", query, "paper result"),
            )
        if call.name == "web.search":
            return ToolExecutionResult(
                tool_name="web.search",
                purpose=call.purpose,
                success=True,
                summary=f"web result for {query}",
                raw={"results": []},
                citations=[
                    ScholarCitation(
                        citation_id="CIT-WEB-1",
                        title="Web",
                        url="https://example.com/post",
                        snippet="snippet",
                        source_type="web",
                    )
                ],
                trace=_trace("web.search:1", query, "web result"),
            )
        if call.name == "code.exec":
            return ToolExecutionResult(
                tool_name="code.exec",
                purpose=call.purpose,
                success=True,
                summary="simulation completed",
                raw={"stdout": "ok"},
                citations=[],
                trace=_trace("code.exec:1", query, "simulation completed"),
            )
        raise AssertionError(f"Unexpected call: {call.name}")


@pytest.mark.asyncio
async def test_academic_beam_prefers_paper_before_web() -> None:
    """Academic task should prioritize paper.search before web.search."""

    decision = ResearchDecision(
        sufficient=False,
        should_compare=False,
        compare_dimensions=[],
        followup_questions=[],
        rationale="need more evidence",
        tool_calls=[
            {"name": "web.search", "parameters": {"query": "gnn drl edge"}},
            {"name": "paper.search", "parameters": {"query": "gnn drl edge"}},
        ],
    )
    router = _RecordingRouter()
    agent = ResearchAgent(
        tool_router=router,
        decision_agent=_DecisionStub(decision),
        min_docs_for_compare=2,
        max_docs_for_compare=4,
        followup_mode="queue",
        max_followup_queries=2,
        enable_web_search=True,
        enable_code_exec=True,
        max_code_exec_snippets=1,
        max_tool_calls=2,
        max_decision_rounds=1,
        min_evidence_quality_score=0,
        action_beam_width=2,
        academic_paper_first=True,
    )
    block = TopicBlock(
        block_id="B1",
        title="GNN+DRL edge computing paper survey",
        question="What are top paper-backed methods for edge offloading?",
        depth=1,
    )

    await agent.research_block(
        block=block,
        session_id="S1",
        user_id=1,
        top_k=5,
        index_mode="auto",
        language="en",
        use_web_search=True,
        use_paper_search=True,
        use_code_exec=False,
    )

    paper_idx = next(i for i, call in enumerate(router.calls) if call.startswith("paper.search:"))
    web_idx = next(i for i, call in enumerate(router.calls) if call.startswith("web.search:"))
    assert paper_idx < web_idx


@pytest.mark.asyncio
async def test_code_exec_auto_enables_when_decision_requests_simulation() -> None:
    """code.exec should run even when request flag is false if decision strongly indicates it."""

    decision = ResearchDecision(
        sufficient=False,
        should_compare=False,
        compare_dimensions=[],
        followup_questions=[],
        rationale="run simulation to validate latency under load",
        tool_calls=[{"name": "code.exec", "parameters": {"code": "print(1+1)"}}],
    )
    router = _RecordingRouter()
    agent = ResearchAgent(
        tool_router=router,
        decision_agent=_DecisionStub(decision),
        min_docs_for_compare=2,
        max_docs_for_compare=4,
        followup_mode="queue",
        max_followup_queries=2,
        enable_web_search=False,
        enable_code_exec=True,
        max_code_exec_snippets=1,
        max_tool_calls=2,
        max_decision_rounds=1,
        min_evidence_quality_score=0,
        action_beam_width=2,
        enable_code_exec_auto=True,
    )
    block = TopicBlock(
        block_id="B2",
        title="Edge offloading simulation design",
        question="Need simulation and numerical validation for latency.",
        depth=1,
    )

    await agent.research_block(
        block=block,
        session_id="S1",
        user_id=1,
        top_k=5,
        index_mode="auto",
        language="en",
        use_web_search=False,
        use_paper_search=False,
        use_code_exec=False,
    )

    assert any(call.startswith("code.exec:") for call in router.calls)
