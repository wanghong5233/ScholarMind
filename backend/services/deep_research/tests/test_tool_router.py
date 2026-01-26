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
