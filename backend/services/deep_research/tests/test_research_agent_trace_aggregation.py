"""Tests for trace aggregation in adaptive search flows."""

from __future__ import annotations

import types

import pytest

from agents.decision_agent import ResearchDecision
from agents.research_agent import ResearchAgent
from service.data_structures import ScholarCitation, ToolTrace, ToolType, TopicBlock
from service.tool_router import ToolContext, ToolExecutionResult


def _build_trace(tool_id: str, query: str, summary: str) -> ToolTrace:
    """Create a minimal tool trace for tests."""

    return ToolTrace(
        tool_id=tool_id,
        citation_id="CIT-TRACE",
        tool_type=ToolType.SEARCH,
        query=query,
        raw_answer=summary,
        summary=summary[:200],
    )


class _StubToolRouter:
    """Stub router with deterministic tool responses."""

    async def execute(self, call, _context):  # type: ignore[override]
        if call.name == "web.search":
            query = str(call.parameters.get("query") or "")
            return ToolExecutionResult(
                tool_name="web.search",
                purpose=call.purpose,
                success=True,
                summary=f"web result for {query}",
                raw={
                    "results": [
                        {"url": "https://example.org/a", "title": "A", "snippet": "alpha"},
                        {"url": "https://example.org/b", "title": "B", "snippet": "beta"},
                    ]
                },
                citations=[
                    ScholarCitation(
                        citation_id="CIT-WEB-1",
                        title="A",
                        url="https://example.org/a",
                        snippet="alpha",
                        source_type="web",
                    )
                ],
                trace=_build_trace(f"web.search:{query}", query, f"web result for {query}"),
            )
        if call.name == "web.open_page":
            url = str(call.parameters.get("url") or "")
            summary = f"opened {url}"
            return ToolExecutionResult(
                tool_name="web.open_page",
                purpose=call.purpose,
                success=True,
                summary=summary,
                raw={"url": url, "content": "page content"},
                citations=[],
                trace=_build_trace(f"web.open_page:{url}", url, summary),
            )
        if call.name == "web.find_in_page":
            url = str(call.parameters.get("url") or "")
            query = str(call.parameters.get("query") or "")
            summary = f"found {query} in {url}"
            return ToolExecutionResult(
                tool_name="web.find_in_page",
                purpose=call.purpose,
                success=True,
                summary=summary,
                raw={"url": url, "query": query, "matches": ["snippet"]},
                citations=[],
                trace=_build_trace(f"web.find_in_page:{url}:{query}", f"{query}@{url}", summary),
            )
        if call.name == "paper.search":
            query = str(call.parameters.get("query") or "")
            return ToolExecutionResult(
                tool_name="paper.search",
                purpose=call.purpose,
                success=True,
                summary=f"paper result for {query}",
                raw={"papers": [{"title": "Paper", "url": "https://arxiv.org/abs/1234.5678"}]},
                citations=[
                    ScholarCitation(
                        citation_id=f"CIT-PAPER-{query[:6] or 'X'}",
                        title="Paper",
                        url="https://arxiv.org/abs/1234.5678",
                        snippet="abstract",
                        source_type="arxiv",
                    )
                ],
                trace=_build_trace(f"paper.search:{query}", query, f"paper result for {query}"),
            )
        raise AssertionError(f"Unexpected tool call: {call.name}")


def _build_context() -> ToolContext:
    """Build a minimal tool context for tests."""

    return ToolContext(
        block=TopicBlock(block_id="B1", title="Topic", question="Main question", depth=1),
        session_id="S1",
        user_id=1,
        top_k=5,
        index_mode="auto",
        language="en",
    )


def _build_agent(
    max_tool_calls: int = 6,
    *,
    allow_followup_query_expansion: bool = False,
) -> ResearchAgent:
    """Construct a research agent with the stub router."""

    return ResearchAgent(
        tool_router=_StubToolRouter(),
        decision_agent=types.SimpleNamespace(),
        min_docs_for_compare=2,
        max_docs_for_compare=4,
        followup_mode="queue",
        max_followup_queries=2,
        enable_web_search=True,
        enable_code_exec=False,
        max_code_exec_snippets=1,
        max_tool_calls=max_tool_calls,
        allow_followup_query_expansion=allow_followup_query_expansion,
    )


@pytest.mark.asyncio
async def test_web_search_keeps_all_traces_from_search_and_page_tools():
    """Adaptive web search should keep search + open_page + find_in_page traces."""

    agent = _build_agent(max_tool_calls=6)
    decision = ResearchDecision(
        sufficient=False,
        should_compare=False,
        compare_dimensions=[],
        followup_questions=["followup about benchmarks"],
        rationale="test",
        tool_calls=[{"name": "web.search", "parameters": {"query": "gnn drl edge computing"}}],
    )

    summary, citations, last_trace, traces = await agent._maybe_web_search(  # pylint: disable=protected-access
        context=_build_context(),
        use_web_search=True,
        decision=decision,
    )

    assert summary is not None
    assert len(citations) >= 1
    assert last_trace is not None
    tool_ids = [trace.tool_id for trace in traces]
    assert any(tool_id.startswith("web.search:") for tool_id in tool_ids)
    assert any(tool_id.startswith("web.open_page:") for tool_id in tool_ids)
    assert any(tool_id.startswith("web.find_in_page:") for tool_id in tool_ids)
    assert len(traces) >= 3


@pytest.mark.asyncio
async def test_paper_search_keeps_all_search_traces():
    """Adaptive paper search should keep each call trace, not only the last one."""

    agent = _build_agent(max_tool_calls=6, allow_followup_query_expansion=True)
    decision = ResearchDecision(
        sufficient=False,
        should_compare=False,
        compare_dimensions=[],
        followup_questions=["latest benchmark datasets"],
        rationale="test",
        tool_calls=[{"name": "paper.search", "parameters": {"query": "gnn drl edge computing"}}],
    )

    summary, citations, last_trace, traces = await agent._maybe_paper_search(  # pylint: disable=protected-access
        context=_build_context(),
        use_paper_search=True,
        decision=decision,
    )

    assert summary is not None
    assert len(citations) >= 1
    assert last_trace is not None
    assert len(traces) >= 2
    assert all(trace.tool_id.startswith("paper.search:") for trace in traces)
