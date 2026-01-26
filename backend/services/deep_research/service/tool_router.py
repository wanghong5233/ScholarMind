"""Tool router for executing research tools."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from service.data_structures import ScholarCitation, ToolTrace
from service.tools.base_tool import ToolContext, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Tool call request."""

    name: str
    parameters: Dict[str, Any]
    purpose: str = "default"


@dataclass
class ToolExecutionResult:
    """Tool execution output with metadata."""

    tool_name: str
    purpose: str
    success: bool
    summary: str
    raw: Dict[str, Any]
    citations: List[ScholarCitation]
    trace: Optional[ToolTrace]
    error: Optional[str] = None


class ToolRouter:
    """Dispatch tool calls to the registry."""

    def __init__(self, registry: ToolRegistry) -> None:
        """Initialize the router with a registry."""

        self._registry = registry

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolExecutionResult:
        """Execute a single tool call."""

        tool = self._registry.get_tool(call.name)
        if not tool:
            return ToolExecutionResult(
                tool_name=call.name,
                purpose=call.purpose,
                success=False,
                summary="Tool not found.",
                raw={},
                citations=[],
                trace=None,
                error="tool_not_found",
            )
        if not tool.validate_parameters(call.parameters):
            return ToolExecutionResult(
                tool_name=call.name,
                purpose=call.purpose,
                success=False,
                summary="Invalid tool parameters.",
                raw={},
                citations=[],
                trace=None,
                error="invalid_parameters",
            )

        try:
            result: ToolResult = await tool.execute(context, call.parameters)
        except Exception as exc:  # noqa: BLE001 - guard tool failures
            logger.exception("Tool execution failed: %s", call.name)
            return ToolExecutionResult(
                tool_name=call.name,
                purpose=call.purpose,
                success=False,
                summary="Tool execution failed.",
                raw={},
                citations=[],
                trace=None,
                error=str(exc),
            )

        return ToolExecutionResult(
            tool_name=call.name,
            purpose=call.purpose,
            success=result.success,
            summary=result.summary,
            raw=result.raw,
            citations=result.citations,
            trace=result.trace,
            error=result.error,
        )

    async def execute_plan(self, calls: List[ToolCall], context: ToolContext) -> List[ToolExecutionResult]:
        """Execute a list of tool calls sequentially."""

        results: List[ToolExecutionResult] = []
        for call in calls:
            results.append(await self.execute(call, context))
        return results
