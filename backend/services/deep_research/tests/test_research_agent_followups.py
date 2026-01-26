"""Tests for inline follow-up execution."""

import types

import pytest

from agents.decision_agent import DecisionAgent, ResearchDecision
from agents.research_agent import ResearchAgent
from service.citation_manager import AsyncCitationManagerWrapper, CitationManager
from service.rag_client import RAGAnswer
from service.tool_registry import create_tool_registry
from service.tool_router import ToolRouter


class DummyRAGClient:
    """Minimal mock of RAGClient for follow-up testing."""

    def __init__(self) -> None:
        self.calls = []

    async def ask(self, session_id, question, user_id, top_k=None, index_mode=None):
        self.calls.append(question)
        return RAGAnswer(answer=f"Answer for {question}", citations=[], chunks=[], raw={})

    async def compare(self, session_id, payload, user_id):
        return {"answer": "Compare", "citations": []}


class DummyDecisionAgent(DecisionAgent):
    """DecisionAgent override that returns deterministic follow-ups."""

    async def decide(self, topic, summary, citations_count, language):  # type: ignore[override]
        return ResearchDecision(
            sufficient=False,
            should_compare=False,
            compare_dimensions=[],
            followup_questions=["Follow-up A", "Follow-up B"],
            rationale="test",
        )


async def _build_agent():
    rag_client = DummyRAGClient()
    manager = CitationManager("test")
    decision_agent = DummyDecisionAgent(
        llm_client=types.SimpleNamespace(is_configured=lambda: False),
        enabled=False,
        min_summary_chars=10,
        min_citations=0,
        max_followups=2,
        compare_dimensions_en=[],
        compare_dimensions_zh=[],
        available_tools=[],
    )
    registry = create_tool_registry(
        rag_client=rag_client,
        citation_manager=AsyncCitationManagerWrapper(manager),
        web_search_client=None,
        code_exec_client=None,
        web_search_max_results=3,
    )
    agent = ResearchAgent(
        tool_router=ToolRouter(registry),
        decision_agent=decision_agent,
        min_docs_for_compare=2,
        max_docs_for_compare=4,
        followup_mode="inline",
        max_followup_queries=2,
        enable_web_search=False,
        enable_code_exec=False,
        max_code_exec_snippets=1,
        max_tool_calls=4,
    )
    return agent, rag_client


@pytest.mark.asyncio
async def test_inline_followups_execute():
    """Inline follow-ups should trigger extra ask calls."""

    agent, rag_client = await _build_agent()
    from service.data_structures import TopicBlock

    block = TopicBlock(block_id="B001", title="Topic", question="Main", depth=1)
    result = await agent.research_block(
        block=block,
        session_id="session_x",
        user_id=1,
        top_k=3,
        index_mode="auto",
        language="en",
        use_web_search=False,
        use_code_exec=False,
    )
    assert len(result.followup_answers) == 2
    assert len(rag_client.calls) == 3  # main + two follow-ups
