"""Tests for the tool router and registry."""

import pytest

from service.data_structures import ToolType
from service.data_structures import TopicBlock
from service.tools.base_tool import BaseTool, ToolContext, ToolRegistry, ToolResult
from service.tool_router import ToolCall, ToolRouter


class EchoTool(BaseTool):
    """Simple tool that echoes input."""

    def __init__(self) -> None:
        super().__init__(
            name="echo",
            description="Echo tool",
            tool_type=ToolType.NOTE,
            parameters_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        )

    async def execute(self, context: ToolContext, parameters):
        return ToolResult(
            success=True,
            summary=parameters.get("text", ""),
            raw={"text": parameters.get("text", "")},
            citations=[],
            trace=None,
        )


class CountingWebSearchTool(BaseTool):
    """Web-search-like tool with execution counter."""

    def __init__(self) -> None:
        super().__init__(
            name="web.search",
            description="Mock web search",
            tool_type=ToolType.SEARCH,
            parameters_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        self.calls = 0

    async def execute(self, context: ToolContext, parameters):
        self.calls += 1
        query = str(parameters.get("query") or "").strip()
        return ToolResult(
            success=True,
            summary=f"result:{query}",
            raw={"query": query, "calls": self.calls},
            citations=[],
            trace=None,
        )


@pytest.mark.asyncio
async def test_tool_router_executes_tool():
    """Router should execute registered tools."""

    registry = ToolRegistry()
    registry.register(EchoTool())
    router = ToolRouter(registry)
    context = ToolContext(
        block=TopicBlock(block_id="B1", title="T", question="Q", depth=1),
        session_id="s1",
        user_id=1,
        top_k=None,
        index_mode=None,
        language="en",
    )
    result = await router.execute(ToolCall(name="echo", parameters={"text": "hi"}), context)
    assert result.success is True
    assert result.summary == "hi"


@pytest.mark.asyncio
async def test_tool_router_missing_tool():
    """Missing tool should return failure."""

    router = ToolRouter(ToolRegistry())
    context = ToolContext(
        block=TopicBlock(block_id="B1", title="T", question="Q", depth=1),
        session_id="s1",
        user_id=1,
        top_k=None,
        index_mode=None,
        language="en",
    )
    result = await router.execute(ToolCall(name="missing", parameters={}), context)
    assert result.success is False


@pytest.mark.asyncio
async def test_tool_router_caches_cacheable_calls_per_block():
    """Cacheable tools should reuse same-params result in one block."""

    registry = ToolRegistry()
    tool = CountingWebSearchTool()
    registry.register(tool)
    observed = []
    router = ToolRouter(registry, observer=observed.append)

    context = ToolContext(
        block=TopicBlock(block_id="B1", title="T1", question="Q1", depth=1),
        session_id="s1",
        user_id=1,
        top_k=None,
        index_mode=None,
        language="en",
    )
    first = await router.execute(ToolCall(name="web.search", parameters={"query": "edge drl"}), context)
    second = await router.execute(ToolCall(name="web.search", parameters={"query": "edge drl"}), context)
    assert first.success is True
    assert second.success is True
    assert tool.calls == 1
    assert second.summary == first.summary

    cache_hits = [
        event
        for event in observed
        if event.get("event_type") == "tool.completed" and bool(event.get("cache_hit"))
    ]
    assert len(cache_hits) >= 1

    another_block = ToolContext(
        block=TopicBlock(block_id="B2", title="T2", question="Q2", depth=1),
        session_id="s1",
        user_id=1,
        top_k=None,
        index_mode=None,
        language="en",
    )
    await router.execute(ToolCall(name="web.search", parameters={"query": "edge drl"}), another_block)
    assert tool.calls == 2
