"""Tool router for executing research tools."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

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

    def __init__(
        self,
        registry: ToolRegistry,
        observer: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        """Initialize the router with a registry."""

        self._registry = registry
        self._observer = observer
        self._cacheable_tools = {"web.search", "web.open_page", "web.find_in_page", "paper.search"}
        self._execution_cache: Dict[str, ToolExecutionResult] = {}

    @staticmethod
    def _clone_result(result: ToolExecutionResult) -> ToolExecutionResult:
        """Clone cached results so downstream mutations stay isolated."""

        return ToolExecutionResult(
            tool_name=result.tool_name,
            purpose=result.purpose,
            success=bool(result.success),
            summary=str(result.summary or ""),
            raw=dict(result.raw or {}),
            citations=list(result.citations or []),
            trace=result.trace,
            error=result.error,
        )

    def _build_cache_key(self, *, call: ToolCall, context: ToolContext) -> Optional[str]:
        """Build a deterministic cache key for idempotent tool calls."""

        tool_name = str(call.name or "").strip().lower()
        if tool_name not in self._cacheable_tools:
            return None
        payload = {
            "tool": tool_name,
            "block_id": str(context.block.block_id or ""),
            "session_id": str(context.session_id or ""),
            "user_id": int(context.user_id),
            "parameters": call.parameters or {},
        }
        try:
            return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        except TypeError:
            logger.warning("Tool cache key fallback to repr for non-serializable payload: %s", payload)
            return repr(payload)

    def _notify(self, payload: Dict[str, Any]) -> None:
        """Notify the optional observer about tool lifecycle events."""

        if not self._observer:
            return
        try:
            self._observer(payload)
        except Exception:  # noqa: BLE001 - observer failures must not break tool execution
            logger.exception("Tool observer callback failed.")

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolExecutionResult:
        """Execute a single tool call."""

        cache_key = self._build_cache_key(call=call, context=context)
        self._notify(
            {
                "event_type": "tool.started",
                "tool": call.name,
                "purpose": call.purpose,
                "parameters": call.parameters,
                "block_id": context.block.block_id,
                "block_title": context.block.title,
            }
        )
        if cache_key:
            cached = self._execution_cache.get(cache_key)
            if cached is not None:
                cached_result = self._clone_result(cached)
                event_type = "tool.completed" if cached_result.success else "tool.failed"
                self._notify(
                    {
                        "event_type": event_type,
                        "tool": call.name,
                        "purpose": call.purpose,
                        "parameters": call.parameters,
                        "success": cached_result.success,
                        "error": cached_result.error,
                        "tool_id": cached_result.trace.tool_id if cached_result.trace else None,
                        "tool_type": cached_result.trace.tool_type.value if cached_result.trace else None,
                        "query": cached_result.trace.query if cached_result.trace else None,
                        "summary": cached_result.summary,
                        "cache_hit": True,
                        "block_id": context.block.block_id,
                        "block_title": context.block.title,
                    }
                )
                return cached_result

        tool = self._registry.get_tool(call.name)
        if not tool:
            self._notify(
                {
                    "event_type": "tool.failed",
                    "tool": call.name,
                    "purpose": call.purpose,
                    "parameters": call.parameters,
                    "success": False,
                    "error": "tool_not_found",
                    "block_id": context.block.block_id,
                    "block_title": context.block.title,
                }
            )
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
            self._notify(
                {
                    "event_type": "tool.failed",
                    "tool": call.name,
                    "purpose": call.purpose,
                    "parameters": call.parameters,
                    "success": False,
                    "error": "invalid_parameters",
                    "block_id": context.block.block_id,
                    "block_title": context.block.title,
                }
            )
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
            self._notify(
                {
                    "event_type": "tool.failed",
                    "tool": call.name,
                    "purpose": call.purpose,
                    "parameters": call.parameters,
                    "success": False,
                    "error": str(exc),
                    "block_id": context.block.block_id,
                    "block_title": context.block.title,
                }
            )
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

        event_type = "tool.completed" if result.success else "tool.failed"
        self._notify(
            {
                "event_type": event_type,
                "tool": call.name,
                "purpose": call.purpose,
                "parameters": call.parameters,
                "success": result.success,
                "error": result.error,
                "tool_id": result.trace.tool_id if result.trace else None,
                "tool_type": result.trace.tool_type.value if result.trace else None,
                "query": result.trace.query if result.trace else None,
                "summary": result.summary,
                "block_id": context.block.block_id,
                "block_title": context.block.title,
            }
        )

        final_result = ToolExecutionResult(
            tool_name=call.name,
            purpose=call.purpose,
            success=result.success,
            summary=result.summary,
            raw=result.raw,
            citations=result.citations,
            trace=result.trace,
            error=result.error,
        )
        if cache_key and final_result.success:
            self._execution_cache[cache_key] = self._clone_result(final_result)
        return final_result

    async def execute_plan(self, calls: List[ToolCall], context: ToolContext) -> List[ToolExecutionResult]:
        """Execute a list of tool calls sequentially."""

        results: List[ToolExecutionResult] = []
        for call in calls:
            results.append(await self.execute(call, context))
        return results
