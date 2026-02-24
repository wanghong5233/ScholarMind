"""Tests for closed-loop decisioning in ResearchAgent."""

from __future__ import annotations

import pytest

from agents.decision_agent import ResearchDecision
from agents.research_agent import ResearchAgent
from service.data_structures import ScholarCitation, ToolTrace, ToolType, TopicBlock
from service.tool_router import ToolContext, ToolExecutionResult


def _trace(tool_id: str, query: str, summary: str) -> ToolTrace:
    """Build a minimal trace payload for tests."""

    return ToolTrace(
        tool_id=tool_id,
        citation_id="CIT-X",
        tool_type=ToolType.SEARCH,
        query=query,
        raw_answer=summary,
        summary=summary[:200],
    )


class _SequencedDecisionAgent:
    """Decision agent stub that returns decisions in sequence."""

    def __init__(self, decisions: list[ResearchDecision]) -> None:
        self._decisions = decisions
        self.calls = 0

    async def decide(self, *args, **kwargs):  # type: ignore[override]
        idx = min(self.calls, len(self._decisions) - 1)
        self.calls += 1
        return self._decisions[idx]


class _ClosedLoopRouter:
    """Tool router stub that records executed calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, call, context):  # type: ignore[override]
        query_text = str(
            call.parameters.get("query")
            or call.parameters.get("question")
            or call.parameters.get("url")
            or ""
        ).strip()
        self.calls.append(f"{call.name}:{query_text}")
        if call.name == "rag.ask":
            return ToolExecutionResult(
                tool_name="rag.ask",
                purpose=call.purpose,
                success=True,
                summary="short base summary",
                raw={"citations": []},
                citations=[],
                trace=_trace("rag.ask:main", query_text or "main", "short base summary"),
            )
        if call.name == "web.search":
            summary = f"web result for {query_text}"
            return ToolExecutionResult(
                tool_name="web.search",
                purpose=call.purpose,
                success=True,
                summary=summary,
                raw={
                    "results": [
                        {
                            "title": "Edge Study",
                            "url": "https://example.org/edge",
                            "snippet": "edge computing evidence",
                        }
                    ]
                },
                citations=[
                    ScholarCitation(
                        citation_id="CIT-WEB-1",
                        title="Edge Study",
                        url="https://example.org/edge",
                        snippet="edge computing evidence",
                        source_type="web",
                    )
                ],
                trace=_trace(f"web.search:{query_text}", query_text, summary),
            )
        if call.name == "web.open_page":
            url = str(call.parameters.get("url") or "")
            return ToolExecutionResult(
                tool_name="web.open_page",
                purpose=call.purpose,
                success=True,
                summary=f"opened {url}",
                raw={"url": url, "content": "opened content"},
                citations=[],
                trace=_trace(f"web.open_page:{url}", url, f"opened {url}"),
            )
        if call.name == "web.find_in_page":
            url = str(call.parameters.get("url") or "")
            query = str(call.parameters.get("query") or "")
            return ToolExecutionResult(
                tool_name="web.find_in_page",
                purpose=call.purpose,
                success=True,
                summary=f"matched {query} on {url}",
                raw={"matches": ["match"]},
                citations=[],
                trace=_trace(f"web.find_in_page:{url}:{query}", f"{query}@{url}", "match"),
            )
        if call.name == "rag.compare":
            return ToolExecutionResult(
                tool_name="rag.compare",
                purpose=call.purpose,
                success=True,
                summary="compare summary",
                raw={},
                citations=[],
                trace=_trace("rag.compare:main", "compare", "compare summary"),
            )
        raise AssertionError(f"Unexpected call: {call.name}")


class _WebFailureRouter:
    """Router stub where web.search always fails."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, call, context):  # type: ignore[override]
        query_text = str(
            call.parameters.get("query")
            or call.parameters.get("question")
            or call.parameters.get("url")
            or ""
        ).strip()
        self.calls.append(f"{call.name}:{query_text}")
        if call.name == "rag.ask":
            return ToolExecutionResult(
                tool_name="rag.ask",
                purpose=call.purpose,
                success=True,
                summary="base summary",
                raw={"citations": []},
                citations=[],
                trace=_trace("rag.ask:main", query_text or "main", "base summary"),
            )
        if call.name == "web.search":
            return ToolExecutionResult(
                tool_name="web.search",
                purpose=call.purpose,
                success=False,
                summary="web transient error",
                raw={},
                citations=[],
                trace=None,
                error="web transient error",
            )
        raise AssertionError(f"Unexpected call: {call.name}")


def _context() -> ToolContext:
    """Build a minimal context for block research."""

    return ToolContext(
        block=TopicBlock(block_id="B1", title="Topic", question="Main question", depth=1),
        session_id="S1",
        user_id=1,
        top_k=5,
        index_mode="auto",
        language="en",
    )


@pytest.mark.asyncio
async def test_closed_loop_runs_multiple_decision_rounds_until_sufficient():
    """Agent should iterate decisions and stop when sufficient."""

    decision_agent = _SequencedDecisionAgent(
        decisions=[
            ResearchDecision(
                sufficient=False,
                should_compare=False,
                compare_dimensions=[],
                followup_questions=[],
                rationale="round1-insufficient",
                tool_calls=[{"name": "web.search", "parameters": {"query": "gnn drl edge"}}],
            ),
            ResearchDecision(
                sufficient=True,
                should_compare=False,
                compare_dimensions=[],
                followup_questions=[],
                rationale="round2-sufficient",
                tool_calls=[],
            ),
        ]
    )
    router = _ClosedLoopRouter()
    agent = ResearchAgent(
        tool_router=router,
        decision_agent=decision_agent,
        min_docs_for_compare=2,
        max_docs_for_compare=4,
        followup_mode="queue",
        max_followup_queries=2,
        enable_web_search=True,
        enable_code_exec=False,
        max_code_exec_snippets=1,
        max_tool_calls=8,
        max_decision_rounds=3,
        min_evidence_quality_score=0,
    )
    result = await agent.research_block(
        block=_context().block,
        session_id="S1",
        user_id=1,
        top_k=5,
        index_mode="auto",
        language="en",
        use_web_search=True,
        use_paper_search=False,
        use_code_exec=False,
    )
    assert result.decision is not None
    assert result.decision.sufficient is True
    assert len(result.decision_history) >= 2
    assert any(call.startswith("web.search:") for call in router.calls)


@pytest.mark.asyncio
async def test_no_implicit_search_injection_when_decision_has_no_tool_calls():
    """Strict mode should not inject hidden fallback tool calls."""

    decision_agent = _SequencedDecisionAgent(
        decisions=[
            ResearchDecision(
                sufficient=True,
                should_compare=False,
                compare_dimensions=[],
                followup_questions=[],
                rationale="premature-sufficient",
                tool_calls=[],
            ),
            ResearchDecision(
                sufficient=True,
                should_compare=False,
                compare_dimensions=[],
                followup_questions=[],
                rationale="second-check",
                tool_calls=[],
            ),
        ]
    )
    router = _ClosedLoopRouter()
    agent = ResearchAgent(
        tool_router=router,
        decision_agent=decision_agent,
        min_docs_for_compare=2,
        max_docs_for_compare=4,
        followup_mode="queue",
        max_followup_queries=2,
        enable_web_search=True,
        enable_code_exec=False,
        max_code_exec_snippets=1,
        max_tool_calls=8,
        max_decision_rounds=2,
        min_evidence_quality_score=95,
    )
    result = await agent.research_block(
        block=_context().block,
        session_id="S1",
        user_id=1,
        top_k=5,
        index_mode="auto",
        language="en",
        use_web_search=True,
        use_paper_search=False,
        use_code_exec=False,
    )
    assert not any(call.startswith("web.search:") for call in router.calls)
    assert "quality_gate_boost" not in str(result.decision_history[0].get("rationale") or "")
    assert result.decision_history


@pytest.mark.asyncio
async def test_tool_failure_is_soft_by_default():
    """Tool failure should be recorded and skipped in non fail-fast mode."""

    decision_agent = _SequencedDecisionAgent(
        decisions=[
            ResearchDecision(
                sufficient=False,
                should_compare=False,
                compare_dimensions=[],
                followup_questions=[],
                rationale="need-web",
                tool_calls=[{"name": "web.search", "parameters": {"query": "gnn drl"}}],
            ),
            ResearchDecision(
                sufficient=True,
                should_compare=False,
                compare_dimensions=[],
                followup_questions=[],
                rationale="enough",
                tool_calls=[],
            ),
        ]
    )
    router = _WebFailureRouter()
    agent = ResearchAgent(
        tool_router=router,
        decision_agent=decision_agent,
        min_docs_for_compare=2,
        max_docs_for_compare=4,
        followup_mode="queue",
        max_followup_queries=2,
        enable_web_search=True,
        enable_code_exec=False,
        max_code_exec_snippets=1,
        max_tool_calls=6,
        max_decision_rounds=2,
        min_evidence_quality_score=0,
    )

    result = await agent.research_block(
        block=_context().block,
        session_id="S1",
        user_id=1,
        top_k=5,
        index_mode="auto",
        language="en",
        use_web_search=True,
        use_paper_search=False,
        use_code_exec=False,
    )

    assert result.summary
    assert any(call.startswith("web.search:") for call in router.calls)


@pytest.mark.asyncio
async def test_tool_failure_raises_when_fail_fast_enabled():
    """Fail-fast mode should still raise on tool error."""

    decision_agent = _SequencedDecisionAgent(
        decisions=[
            ResearchDecision(
                sufficient=False,
                should_compare=False,
                compare_dimensions=[],
                followup_questions=[],
                rationale="need-web",
                tool_calls=[{"name": "web.search", "parameters": {"query": "gnn drl"}}],
            ),
        ]
    )
    router = _WebFailureRouter()
    agent = ResearchAgent(
        tool_router=router,
        decision_agent=decision_agent,
        min_docs_for_compare=2,
        max_docs_for_compare=4,
        followup_mode="queue",
        max_followup_queries=2,
        enable_web_search=True,
        enable_code_exec=False,
        max_code_exec_snippets=1,
        max_tool_calls=6,
        max_decision_rounds=1,
        min_evidence_quality_score=0,
        fail_fast_on_tool_error=True,
    )

    with pytest.raises(RuntimeError, match="web.search failed"):
        await agent.research_block(
            block=_context().block,
            session_id="S1",
            user_id=1,
            top_k=5,
            index_mode="auto",
            language="en",
            use_web_search=True,
            use_paper_search=False,
            use_code_exec=False,
        )
