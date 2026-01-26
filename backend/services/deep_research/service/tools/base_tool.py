"""Base classes for DeepResearch tools."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from service.data_structures import ScholarCitation, ToolTrace, ToolType, TopicBlock

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """Execution context passed to each tool."""

    block: TopicBlock
    session_id: Optional[str]
    user_id: int
    top_k: Optional[int]
    index_mode: Optional[str]
    language: Optional[str]


@dataclass
class ToolResult:
    """Normalized tool execution output."""

    success: bool
    summary: str
    raw: Dict[str, Any]
    citations: List[ScholarCitation]
    trace: Optional[ToolTrace]
    error: Optional[str] = None


class BaseTool(ABC):
    """Base class for all DeepResearch tools."""

    def __init__(
        self,
        name: str,
        description: str,
        tool_type: ToolType,
        parameters_schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize a tool with metadata.

        Args:
            name (str): Tool name.
            description (str): Tool description.
            tool_type (ToolType): Tool category.
            parameters_schema (Optional[Dict[str, Any]]): JSON schema for parameters.
        """

        self.name = name
        self.description = description
        self.tool_type = tool_type
        self.parameters_schema = parameters_schema or {
            "type": "object",
            "properties": {},
            "required": [],
        }

    @abstractmethod
    async def execute(self, context: ToolContext, parameters: Dict[str, Any]) -> ToolResult:
        """Execute the tool with the given context."""

    def validate_parameters(self, parameters: Dict[str, Any]) -> bool:
        """Validate tool parameters.

        Args:
            parameters (Dict[str, Any]): Tool parameters.

        Returns:
            bool: Whether parameters pass basic validation.
        """

        required = self.parameters_schema.get("required", [])
        return all(key in parameters for key in required)


class ToolRegistry:
    """Registry for managing available tools."""

    def __init__(self) -> None:
        """Initialize an empty registry."""

        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a new tool.

        Args:
            tool (BaseTool): Tool to register.
        """

        self._tools[tool.name] = tool
        logger.info("Registered tool: %s", tool.name)

    def get_tool(self, tool_name: str) -> Optional[BaseTool]:
        """Fetch a tool by name."""

        return self._tools.get(tool_name)

    def list_tools(self) -> List[Dict[str, str]]:
        """List all registered tools."""

        return [{"name": tool.name, "description": tool.description} for tool in self._tools.values()]

    def get_tools_for_llm(self) -> List[Dict[str, Any]]:
        """Return tool descriptions for LLM tool calling."""

        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_schema,
                },
            }
            for tool in self._tools.values()
        ]
