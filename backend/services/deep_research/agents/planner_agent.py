"""Planner agent for generating topic queues."""

from dataclasses import dataclass
import json
from typing import Any, List, Optional

from utils.language import guess_language


@dataclass
class PlanItem:
    """Plan item produced by the planner."""

    title: str
    question: str
    depth: int
    parent_title: Optional[str] = None


class PlannerAgent:
    """Generate a queue plan for DeepResearch."""

    def __init__(self, depth: int, breadth: int, language: Optional[str] = None) -> None:
        """Initialize the planner with depth and breadth limits.

        Args:
            depth (int): Maximum depth of the plan.
            breadth (int): Maximum number of level-one topics.
            language (Optional[str]): Preferred output language.
        """

        self.depth = max(1, depth)
        self.breadth = max(1, breadth)
        self.language = language

    def plan(self, topic: str) -> List[PlanItem]:
        """Generate plan items for the given topic.

        Args:
            topic (str): Research topic.

        Returns:
            List[PlanItem]: Planned items derived from templates.
        """

        language = self.language or guess_language(topic)
        level_one = self._level_one_templates(topic, language)
        items: List[PlanItem] = []

        for title in level_one[: self.breadth]:
            items.append(PlanItem(title=title, question=title, depth=1))

        if self.depth <= 1:
            return items

        for parent in level_one[: self.breadth]:
            for subtopic in self._level_two_templates(parent, language):
                items.append(
                    PlanItem(
                        title=subtopic,
                        question=subtopic,
                        depth=2,
                        parent_title=parent,
                    )
                )
        return items

    async def plan_with_rag(
        self,
        topic: str,
        rag_client: Any,
        session_id: str,
        user_id: int,
        top_k: Optional[int] = None,
        index_mode: Optional[str] = None,
    ) -> List[PlanItem]:
        """Generate plan items using ScholarMind RAG.

        Args:
            topic (str): Research topic.
            rag_client (Any): RAG client to call ScholarMind.
            session_id (str): ScholarMind session id.
            user_id (int): ScholarMind user id.
            top_k (Optional[int]): Retrieval top_k override.
            index_mode (Optional[str]): Retrieval index mode.

        Returns:
            List[PlanItem]: Planned items from the LLM response.
        """

        prompt = self._build_prompt(topic)
        answer = await rag_client.ask(
            session_id=session_id,
            question=prompt,
            user_id=user_id,
            top_k=top_k,
            index_mode=index_mode,
        )
        items = self._parse_plan_items(answer.answer)
        if items:
            return items
        return self.plan(topic)

    def _level_one_templates(self, topic: str, language: str) -> List[str]:
        """Generate level-one planning items.

        Args:
            topic (str): Research topic.
            language (str): Output language code.

        Returns:
            List[str]: Level-one plan titles.
        """

        if language == "zh":
            return [
                f"{topic} 的背景与核心定义",
                f"{topic} 的关键方法与核心机制",
                f"{topic} 的代表性论文与最新进展",
                f"{topic} 的数据集与评测基准",
                f"{topic} 的应用场景与落地实践",
                f"{topic} 的主要挑战与未来方向",
            ]
        return [
            f"Background and core definitions of {topic}",
            f"Key methods and mechanisms for {topic}",
            f"Representative papers and recent advances on {topic}",
            f"Datasets and benchmarks for {topic}",
            f"Applications and real-world use cases of {topic}",
            f"Challenges and future directions for {topic}",
        ]

    def _level_two_templates(self, parent: str, language: str) -> List[str]:
        """Generate level-two planning items.

        Args:
            parent (str): Parent topic.
            language (str): Output language code.

        Returns:
            List[str]: Level-two plan titles.
        """

        if language == "zh":
            return [
                f"{parent} 的关键证据与引用依据",
                f"{parent} 的局限性与改进空间",
            ]
        return [
            f"Evidence and supporting citations for {parent}",
            f"Limitations and improvement opportunities for {parent}",
        ]

    def _build_prompt(self, topic: str) -> str:
        """Build the planning prompt.

        Args:
            topic (str): Research topic.

        Returns:
            str: Prompt for the planner.
        """

        language = self.language or guess_language(topic)
        max_level_one = self.breadth
        max_depth = self.depth
        if language == "zh":
            return (
                "你是一名研究规划助手。请将用户话题拆解为研究计划。\n"
                f"要求：1) 深度最多 {max_depth}；2) 一级子话题最多 {max_level_one} 个；"
                "3) 输出 JSON 数组，每项包含 title, question, depth, parent_title；"
                "4) depth=1 的 parent_title 为空；5) 只输出 JSON。\n"
                f"话题：{topic}"
            )
        return (
            "You are a research planner. Decompose the topic into a research plan.\n"
            f"Constraints: depth <= {max_depth}; level-1 topics <= {max_level_one}. "
            "Return a JSON array; each item has title, question, depth, parent_title. "
            "For depth=1, parent_title should be null. Output JSON only.\n"
            f"Topic: {topic}"
        )

    def _parse_plan_items(self, text: str) -> List[PlanItem]:
        """Parse plan items from a JSON response.

        Args:
            text (str): Raw LLM output.

        Returns:
            List[PlanItem]: Parsed plan items.
        """

        payload = self._extract_json(text)
        if not isinstance(payload, list):
            return []
        items: List[PlanItem] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            question = str(item.get("question", "")).strip() or title
            depth = int(item.get("depth", 1) or 1)
            parent_title = item.get("parent_title")
            if not title or depth < 1:
                continue
            if depth > 1 and not parent_title:
                parent_title = None
            items.append(
                PlanItem(
                    title=title,
                    question=question,
                    depth=depth,
                    parent_title=parent_title,
                )
            )
        return self._truncate_plan(items)

    def _truncate_plan(self, items: List[PlanItem]) -> List[PlanItem]:
        """Ensure plan respects breadth/depth limits.

        Args:
            items (List[PlanItem]): Parsed plan items.

        Returns:
            List[PlanItem]: Trimmed plan items.
        """

        level_one = [item for item in items if item.depth == 1]
        trimmed_level_one = level_one[: self.breadth]
        allowed_parents = {item.title for item in trimmed_level_one}
        level_two = [
            item
            for item in items
            if item.depth == 2 and item.parent_title in allowed_parents
        ]
        return trimmed_level_one + level_two if self.depth > 1 else trimmed_level_one

    @staticmethod
    def _extract_json(text: str) -> Any:
        """Extract JSON payload from a text blob.

        Args:
            text (str): Raw LLM output.

        Returns:
            Any: Parsed JSON payload or None.
        """

        if not text:
            return None
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
