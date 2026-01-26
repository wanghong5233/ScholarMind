"""Code execution tool for DeepResearch."""

from __future__ import annotations

from typing import Any, Dict, List

from service.code_exec_client import CodeExecClient
from service.data_structures import ToolTrace, ToolType
from service.tools.base_tool import BaseTool, ToolContext, ToolResult


class CodeExecTool(BaseTool):
    """Tool that runs Python snippets for calculations."""

    def __init__(self, exec_client: CodeExecClient) -> None:
        """Initialize the code execution tool."""

        super().__init__(
            name="code.exec",
            description="Execute Python snippets for numeric verification.",
            tool_type=ToolType.CODE,
            parameters_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "language": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 60},
                },
                "required": ["code"],
            },
        )
        self._exec_client = exec_client

    async def execute(self, context: ToolContext, parameters: Dict[str, Any]) -> ToolResult:
        """Execute Python code."""

        code = parameters.get("code") or ""
        language = parameters.get("language") or "python"
        if language.lower() != "python":
            return ToolResult(
                success=False,
                summary="Unsupported language for code execution.",
                raw={},
                citations=[],
                trace=None,
                error="unsupported_language",
            )
        if not code:
            return ToolResult(
                success=False,
                summary="Missing code for execution.",
                raw={},
                citations=[],
                trace=None,
                error="missing_code",
            )

        timeout = parameters.get("timeout")
        result = await self._exec_client.execute(code, timeout=timeout)
        summary = self._format_summary(result)
        trace = self._build_trace(context, code, summary)
        return ToolResult(
            success=bool(result.get("success")),
            summary=summary,
            raw=result,
            citations=[],
            trace=trace,
            error=None if result.get("success") else result.get("stderr"),
        )

    @staticmethod
    def _format_summary(result: Dict[str, Any]) -> str:
        """Format execution output into a summary string."""

        if result.get("success"):
            stdout = result.get("stdout") or ""
            return stdout.strip() or "Execution completed with no output."
        stderr = result.get("stderr") or "Execution failed."
        return stderr.strip()

    @staticmethod
    def _build_trace(context: ToolContext, code: str, summary: str) -> ToolTrace:
        """Build a ToolTrace for code execution."""

        trace = ToolTrace(
            tool_id=f"code.exec:{context.block.block_id}",
            citation_id="NO-CIT",
            tool_type=ToolType.CODE,
            query=code[:200],
            raw_answer=summary,
            summary=summary[:400],
        )
        trace.truncate_raw_answer()
        return trace
