"""Decision agent for tool selection and sufficiency checks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from service.llm_client import LLMClient
from utils.language import guess_language


@dataclass
class ResearchDecision:
    """Decision output for a research step."""

    sufficient: bool
    should_compare: bool
    compare_dimensions: List[str]
    followup_questions: List[str]
    rationale: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the decision for trace storage."""

        return {
            "sufficient": self.sufficient,
            "should_compare": self.should_compare,
            "compare_dimensions": list(self.compare_dimensions),
            "followup_questions": list(self.followup_questions),
            "rationale": self.rationale,
            "tool_calls": list(self.tool_calls),
        }


class DecisionAgent:
    """Determine whether to expand research or call comparison tools."""

    def __init__(
        self,
        llm_client: LLMClient,
        enabled: bool,
        min_summary_chars: int,
        min_citations: int,
        max_followups: int,
        compare_dimensions_en: List[str],
        compare_dimensions_zh: List[str],
        available_tools: Optional[List[str]] = None,
    ) -> None:
        """Initialize the decision agent.

        Args:
            llm_client (LLMClient): LLM client wrapper.
            enabled (bool): Whether to use LLM for decisions.
            min_summary_chars (int): Minimum length to mark sufficient.
            min_citations (int): Minimum citations to mark sufficient.
            max_followups (int): Max follow-up questions to return.
            compare_dimensions_en (List[str]): Default compare dimensions (EN).
            compare_dimensions_zh (List[str]): Default compare dimensions (ZH).
            available_tools (Optional[List[str]]): Tool names available for selection.
        """

        self._llm_client = llm_client
        self._enabled = enabled
        self._min_summary_chars = max(50, min_summary_chars)
        self._min_citations = max(0, min_citations)
        self._max_followups = max(0, max_followups)
        self._compare_dimensions_en = compare_dimensions_en
        self._compare_dimensions_zh = compare_dimensions_zh
        self._available_tools = available_tools or []

    async def decide(
        self,
        topic: str,
        summary: str,
        citations_count: int,
        language: Optional[str],
    ) -> ResearchDecision:
        """Decide whether the result is sufficient and needs comparison.

        Args:
            topic (str): Research topic.
            summary (str): Summary text.
            citations_count (int): Number of citations gathered.
            language (Optional[str]): Language override.

        Returns:
            ResearchDecision: Decision output.
        """

        lang = language or guess_language(topic or summary)
        if not self._enabled or not self._llm_client.is_configured():
            return self._heuristic_decision(summary, citations_count, lang)

        prompt = self._build_prompt(topic, summary, citations_count, lang)
        output = await self._llm_client.generate(prompt)
        parsed = self._parse_output(output)
        if parsed:
            return parsed
        return self._heuristic_decision(summary, citations_count, lang)

    def _heuristic_decision(
        self,
        summary: str,
        citations_count: int,
        language: str,
    ) -> ResearchDecision:
        """Fallback decision logic without LLM.

        Args:
            summary (str): Summary text.
            citations_count (int): Number of citations.
            language (str): Language code.

        Returns:
            ResearchDecision: Decision output.
        """

        sufficient = len(summary or "") >= self._min_summary_chars and citations_count >= self._min_citations
        followups = []
        if not sufficient and self._max_followups > 0:
            followups = self._default_followups(language=language)[: self._max_followups]
        tool_calls = []
        if not sufficient and "web.search" in self._available_tools:
            topic_hint = summary[:80].strip() or "research topic"
            if language == "zh":
                topic_hint = "研究主题"
            tool_calls.append({"name": "web.search", "parameters": {"query": topic_hint}})

        return ResearchDecision(
            sufficient=sufficient,
            should_compare=citations_count >= self._min_citations,
            compare_dimensions=self._default_compare_dimensions(language),
            followup_questions=followups,
            rationale="heuristic",
            tool_calls=tool_calls,
        )

    def _build_prompt(self, topic: str, summary: str, citations_count: int, language: str) -> str:
        """Build the LLM prompt for decisions."""

        dimensions = self._default_compare_dimensions(language)
        tools_hint = ", ".join(self._available_tools) if self._available_tools else "none"
        if language == "zh":
            return (
                "你是研究决策助手，请判断当前研究结果是否充分，并给出下一步。\n"
                "请输出 JSON：{\n"
                "  \"sufficient\": bool,\n"
                "  \"should_compare\": bool,\n"
                "  \"compare_dimensions\": [str],\n"
                "  \"followup_questions\": [str],\n"
                "  \"rationale\": str,\n"
                "  \"tool_calls\": [{\"name\": str, \"parameters\": object}]\n"
                "}\n"
                f"主题：{topic}\n"
                f"摘要：{summary}\n"
                f"引用数：{citations_count}\n"
                f"默认对比维度：{dimensions}\n"
                f"可用工具：{tools_hint}\n"
                "仅输出 JSON。"
            )
        return (
            "You are a research decision assistant. Decide if the result is sufficient and what to do next.\n"
            "Output JSON: {\n"
            "  \"sufficient\": bool,\n"
            "  \"should_compare\": bool,\n"
            "  \"compare_dimensions\": [str],\n"
            "  \"followup_questions\": [str],\n"
            "  \"rationale\": str,\n"
            "  \"tool_calls\": [{\"name\": str, \"parameters\": object}]\n"
            "}\n"
            f"Topic: {topic}\n"
            f"Summary: {summary}\n"
            f"Citations: {citations_count}\n"
            f"Default compare dimensions: {dimensions}\n"
            f"Available tools: {tools_hint}\n"
            "Output JSON only."
        )

    def _parse_output(self, output: Optional[str]) -> Optional[ResearchDecision]:
        """Parse the LLM JSON output."""

        if not output:
            return None
        text = output.strip()
        if text.startswith("```"):
            text = text.strip("`")
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None

        tool_calls = self._normalize_tool_calls(payload.get("tool_calls"))
        return ResearchDecision(
            sufficient=bool(payload.get("sufficient", False)),
            should_compare=bool(payload.get("should_compare", False)),
            compare_dimensions=list(payload.get("compare_dimensions", []) or []),
            followup_questions=list(payload.get("followup_questions", []) or []),
            rationale=str(payload.get("rationale", "")),
            tool_calls=tool_calls,
        )

    def _normalize_tool_calls(self, raw_calls: Any) -> List[Dict[str, Any]]:
        """Normalize and filter tool calls from LLM output."""

        if not isinstance(raw_calls, list):
            return []
        normalized: List[Dict[str, Any]] = []
        for item in raw_calls:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            if self._available_tools and name not in self._available_tools:
                continue
            params = item.get("parameters")
            if not isinstance(params, dict):
                params = {}
            normalized.append({"name": name, "parameters": params})
        return normalized

    def _default_compare_dimensions(self, language: str) -> List[str]:
        """Return default compare dimensions based on language."""

        if language == "zh":
            return list(self._compare_dimensions_zh)
        return list(self._compare_dimensions_en)

    def _default_followups(self, language: str) -> List[str]:
        """Return default follow-up questions when output is insufficient."""

        if language == "zh":
            return [
                "有哪些权威论文或基准支持该结论？",
                "该方向的主要局限性或未解决问题是什么？",
            ]
        return [
            "Which authoritative papers or benchmarks support this claim?",
            "What are the key limitations or open problems?",
        ]
