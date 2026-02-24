"""Tests for PaperSearchTool provider forwarding and normalization."""

import pytest

from service.data_structures import TopicBlock
from service.tools.base_tool import ToolContext
from service.tools.paper_search_tool import PaperSearchTool


class _DummyCitationManager:
    """Minimal async citation manager stub."""

    def __init__(self) -> None:
        self._counter = 0

    async def generate_research_citation_id(self, _source_id: str) -> str:
        self._counter += 1
        return f"C{self._counter:03d}"

    async def add_citation(self, _citation) -> int:  # noqa: ANN001 - test stub
        return self._counter


class _DummyRAGClient:
    """RAG client stub that records provider forwarding arguments."""

    def __init__(self) -> None:
        self.calls = []

    async def get_session_detail(self, session_id, user_id):  # noqa: ANN001 - test stub
        return {"sessionId": session_id, "userId": user_id, "kbId": 1}

    async def search_online_papers(self, **kwargs):  # noqa: ANN003 - test stub
        self.calls.append(dict(kwargs))
        providers = kwargs.get("providers") or []
        if providers == ["semantic_scholar"]:
            return [
                {
                    "title": "Semantic Paper",
                    "source_url": "https://example.org/semantic",
                    "abstract": "Semantic abstract",
                }
            ]
        if providers == ["arxiv"]:
            return [
                {
                    "title": "Arxiv Paper",
                    "source_url": "https://arxiv.org/abs/0000.00000",
                    "abstract": "Arxiv abstract",
                }
            ]
        return []


class _DummyEmptyRAGClient(_DummyRAGClient):
    """RAG client stub that always returns empty provider results."""

    async def search_online_papers(self, **kwargs):  # noqa: ANN003 - test stub
        self.calls.append(dict(kwargs))
        return []


@pytest.mark.asyncio
async def test_paper_search_tool_forwards_providers_and_rank_by():
    """Tool should forward provider/rank_by and normalize provider metadata."""

    rag_client = _DummyRAGClient()
    tool = PaperSearchTool(
        rag_client=rag_client,
        citation_manager=_DummyCitationManager(),
        max_results=5,
        default_providers=["semantic_scholar", "arxiv"],
        default_rank_by="hybrid",
        min_per_provider=1,
        query_rewrite_llm_client=None,
    )
    context = ToolContext(
        block=TopicBlock(block_id="B1", title="Topic", question="Question", depth=1),
        session_id="S1",
        user_id=1,
        top_k=None,
        index_mode="auto",
        language="en",
    )

    result = await tool.execute(context, {"query": "gnn drl edge computing", "limit": 5})

    assert result.success is True
    assert len(result.citations) >= 2
    providers_seen = [call.get("providers") for call in rag_client.calls]
    assert ["semantic_scholar"] in providers_seen
    assert ["arxiv"] in providers_seen
    assert all(call.get("rank_by") == "hybrid" for call in rag_client.calls)

    raw_papers = result.raw.get("papers") or []
    paper_providers = {str(item.get("provider")) for item in raw_papers}
    assert "semantic_scholar" in paper_providers
    assert "arxiv" in paper_providers


@pytest.mark.asyncio
async def test_paper_search_tool_empty_results_are_non_fatal():
    """Empty provider results should not fail the research block."""

    rag_client = _DummyEmptyRAGClient()
    tool = PaperSearchTool(
        rag_client=rag_client,
        citation_manager=_DummyCitationManager(),
        max_results=5,
        default_providers=["semantic_scholar", "arxiv"],
        default_rank_by="hybrid",
        min_per_provider=1,
        query_rewrite_llm_client=None,
    )
    context = ToolContext(
        block=TopicBlock(block_id="B1", title="Topic", question="Question", depth=1),
        session_id="S1",
        user_id=1,
        top_k=None,
        index_mode="auto",
        language="en",
    )

    result = await tool.execute(context, {"query": "very narrow topic", "limit": 5})

    assert result.success is True
    assert result.citations == []
    assert "No relevant papers found" in result.summary
