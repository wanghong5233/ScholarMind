"""Tests for search-query quality and adaptive call expansion."""

import types

import pytest

from agents.decision_agent import DecisionAgent
from agents.research_agent import ResearchAgent
from service.data_structures import TopicBlock
from service.tool_router import ToolCall


class _DummyLLMClient:
    """Minimal LLM client stub for strict decision-agent tests."""

    def is_configured(self) -> bool:
        """Return False to emulate missing LLM configuration."""

        return False

    async def generate(self, *_args, **_kwargs):  # pragma: no cover - should never be called
        """Unreachable stub to satisfy interface."""

        return ""


@pytest.mark.asyncio
async def test_decision_agent_requires_llm_configuration():
    """DecisionAgent should fail fast when LLM is disabled."""

    agent = DecisionAgent(
        llm_client=_DummyLLMClient(),
        enabled=False,
        min_summary_chars=300,
        min_citations=2,
        max_followups=2,
        compare_dimensions_en=["Methodology"],
        compare_dimensions_zh=["方法"],
        available_tools=["web.search"],
    )

    with pytest.raises(RuntimeError, match="Decision LLM is disabled"):
        await agent.decide(
            topic="GNN DRL for edge computing scheduling",
            summary="",
            citations_count=0,
            language="en",
        )


def _build_agent() -> ResearchAgent:
    """Build a minimal agent instance for helper-method tests."""

    return ResearchAgent(
        tool_router=types.SimpleNamespace(),
        decision_agent=types.SimpleNamespace(),
        min_docs_for_compare=2,
        max_docs_for_compare=4,
        followup_mode="queue",
        max_followup_queries=2,
        enable_web_search=True,
        enable_code_exec=False,
        max_code_exec_snippets=1,
        max_tool_calls=4,
    )


def test_research_agent_build_search_calls_drops_generic_query_and_keeps_followups():
    """Builder should reject generic placeholders and keep actionable follow-ups."""

    agent = _build_agent()
    calls = agent._build_search_calls(
        tool_name="web.search",
        decision_calls=[
            ToolCall(
                name="web.search",
                parameters={"query": "research topic"},
                purpose="decision",
            )
        ],
        followup_questions=["What are the latest benchmark datasets for this task?"],
        allow_extra=True,
    )

    assert len(calls) == 1
    query = str(calls[0].parameters.get("query") or "").lower()
    assert "benchmark" in query
    assert "dataset" in query


def test_research_agent_build_search_calls_keeps_decision_query_and_dedupes_followups():
    """Builder should preserve valid decision query and dedupe repeated follow-up query."""

    agent = _build_agent()
    calls = agent._build_search_calls(
        tool_name="paper.search",
        decision_calls=[
            ToolCall(
                name="paper.search",
                parameters={"query": "GNN DRL edge offloading"},
                purpose="decision",
            )
        ],
        followup_questions=[
            "GNN DRL edge offloading",
            "Recent benchmarks for edge scheduling with GNN DRL",
        ],
        allow_extra=True,
    )

    assert len(calls) == 2
    assert calls[0].parameters["query"] == "GNN DRL edge offloading"
    assert calls[1].parameters["query"] == "Recent benchmarks for edge scheduling with GNN DRL"


def test_research_agent_build_search_calls_anchors_generic_query_with_runtime_topic():
    """Generic search intent should be anchored by runtime topic terms."""

    agent = _build_agent()
    calls = agent._build_search_calls(
        tool_name="web.search",
        decision_calls=[
            ToolCall(
                name="web.search",
                parameters={"query": "limitations and future work"},
                purpose="decision",
            )
        ],
        followup_questions=[],
        allow_extra=False,
        anchor_terms=["edge computing", "gnn", "drl", "task offloading"],
    )

    assert len(calls) == 1
    query = str(calls[0].parameters.get("query") or "").lower()
    assert "edge" in query
    assert ("gnn" in query) or ("drl" in query)


def test_research_agent_build_default_search_query_uses_anchor_terms_for_generic_block():
    """Fallback query for generic block titles should include dynamic anchors."""

    agent = _build_agent()
    block = TopicBlock(
        block_id="B001",
        title="局限与未来方向",
        question="有哪些局限与未来方向？",
    )
    query = agent._build_default_search_query(
        block,
        anchor_terms=["mec", "gnn", "drl", "resource allocation"],
    ).lower()

    assert query
    assert ("mec" in query) or ("gnn" in query) or ("drl" in query)


def test_research_agent_dynamic_anchor_terms_include_global_topic():
    """Anchor extraction should include root-topic terms for generic blocks."""

    block = TopicBlock(
        block_id="B002",
        title="局限与未来方向",
        question="该方向有哪些局限和未来方向？",
    )
    terms = ResearchAgent._derive_dynamic_anchor_terms(  # pylint: disable=protected-access
        block=block,
        context_text=None,
        global_topic="GNN DRL for edge computing task offloading",
    )

    normalized = set(terms)
    assert "gnn" in normalized
    assert "drl" in normalized
    assert ("edge" in normalized) or ("offloading" in normalized)
