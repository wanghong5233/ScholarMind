"""Planner agent for generating topic queues."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from utils.language import guess_language
from utils.json_utils import ensure_json_list, extract_json_from_text


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
        """Template planning is disabled for DeepResearch.

        DeepResearch planning must go through LLM-based planner execution.
        """

        _ = topic
        raise RuntimeError("Template planner is disabled. Use LLM planning only.")

    async def plan_with_rag(
        self,
        topic: str,
        rag_client: Any,
        session_id: str,
        user_id: int,
        top_k: Optional[int] = None,
        index_mode: Optional[str] = None,
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        progress_observer: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[PlanItem]:
        """Generate plan items using ScholarMind RAG.

        Args:
            topic (str): Research topic.
            rag_client (Any): RAG client to call ScholarMind.
            session_id (str): ScholarMind session id.
            user_id (int): ScholarMind user id.
            top_k (Optional[int]): Retrieval top_k override.
            index_mode (Optional[str]): Retrieval index mode.
            llm_provider (Optional[str]): Optional ask-level provider override.
            llm_model (Optional[str]): Optional ask-level model override.
            progress_observer (Optional[Callable[[str, Dict[str, Any]], None]]):
                Optional callback for intermediate planning progress.

        Returns:
            List[PlanItem]: Planned items from the LLM response.
        """

        prompt = self._build_prompt(topic)
        self._notify_progress(
            progress_observer,
            "Planner prompt prepared",
            {"depth": self.depth, "breadth": self.breadth},
        )
        self._notify_progress(
            progress_observer,
            "Requesting plan from RAG",
            {"session_id": session_id, "index_mode": index_mode or ""},
        )
        answer = await rag_client.ask(
            session_id=session_id,
            question=prompt,
            user_id=user_id,
            top_k=top_k,
            index_mode=index_mode,
            llm_provider=llm_provider,
            llm_model=llm_model,
            persist_history=False,
        )
        raw_answer = answer.answer or ""
        self._notify_progress(
            progress_observer,
            "RAG response received",
            {"chars": len(raw_answer)},
        )
        items = self._parse_plan_items(raw_answer)
        if items:
            self._notify_progress(
                progress_observer,
                "RAG plan parsed",
                {"items": len(items)},
            )
            return items
        if raw_answer:
            self._notify_progress(
                progress_observer,
                "Primary parse failed, trying JSON repair",
                {},
            )
            repair_prompt = self._build_repair_prompt(topic, raw_answer)
            repaired = await rag_client.ask(
                session_id=session_id,
                question=repair_prompt,
                user_id=user_id,
                top_k=top_k,
                index_mode=index_mode,
                llm_provider=llm_provider,
                llm_model=llm_model,
                persist_history=False,
            )
            repaired_answer = repaired.answer or ""
            self._notify_progress(
                progress_observer,
                "Repair response received",
                {"chars": len(repaired_answer)},
            )
            repaired_items = self._parse_plan_items(repaired_answer)
            if repaired_items:
                self._notify_progress(
                    progress_observer,
                    "Repair parse succeeded",
                    {"items": len(repaired_items)},
                )
                return repaired_items
        self._notify_progress(
            progress_observer,
            "Planner output invalid after repair",
            {"reason": "empty_or_invalid_rag_output"},
        )
        raise ValueError("Planner output invalid after primary+repair passes")

    @staticmethod
    def _notify_progress(
        observer: Optional[Callable[[str, Dict[str, Any]], None]],
        message: str,
        payload: Dict[str, Any],
    ) -> None:
        """Notify optional progress observer safely."""

        if observer is None:
            return
        try:
            observer(message, payload)
        except Exception:
            return

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
                "4) depth=1 的 parent_title 为空；5) 只输出 JSON，不要包含其它文字或代码块。\n"
                "6) ★ title 字段供用户阅读，可用中文；"
                "question 字段是发往 Semantic Scholar / arXiv 等英文学术数据库的检索关键词，"
                "必须用英文学术关键词写成（如 'GNN DRL edge computing offloading'），"
                "不得使用中文，否则检索结果为零。\n"
                "覆盖维度建议：背景定义/核心机制/代表性论文/数据集基准/应用场景/局限与未来。\n"
                f"话题：{topic}"
            )
        return (
            "You are a research planner. Decompose the topic into a research plan.\n"
            f"Constraints: depth <= {max_depth}; level-1 topics <= {max_level_one}. "
            "Return a JSON array; each item has title, question, depth, parent_title. "
            "For depth=1, parent_title should be null. Output JSON only (no extra text).\n"
            "Coverage hints: background/definition, mechanisms, representative papers, datasets/benchmarks, applications, limitations/future work.\n"
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
        payload = ensure_json_list(payload)
        if payload is None:
            return []
        items: List[PlanItem] = []
        seen_keys: set[tuple[int, str, str, Optional[str]]] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            title = self._compact_plan_text(str(item.get("title", "")), max_chars=140)
            question = self._compact_plan_text(
                str(item.get("question", "")) or title,
                max_chars=260,
            ) or title
            depth = int(item.get("depth", 1) or 1)
            parent_title = item.get("parent_title")
            if isinstance(parent_title, str):
                parent_title = self._compact_plan_text(parent_title, max_chars=140)
            elif parent_title is not None:
                parent_title = self._compact_plan_text(str(parent_title), max_chars=140)
            if not title or depth < 1:
                continue
            if depth > 1 and not parent_title:
                parent_title = None
            dedupe_key = (depth, title, question, parent_title if parent_title else None)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
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
        return extract_json_from_text(text)

    @staticmethod
    def _compact_plan_text(text: str, *, max_chars: int) -> str:
        """Normalize and cap a plan text field."""

        normalized = " ".join(str(text or "").split()).strip()
        if not normalized:
            return ""
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max_chars - 3].rstrip() + "..."

    def _build_repair_prompt(self, topic: str, raw_output: str) -> str:
        """Build a repair prompt for malformed JSON output."""

        language = self.language or guess_language(topic)
        truncated = raw_output.strip()
        if len(truncated) > 2000:
            truncated = truncated[:2000] + "..."
        if language == "zh":
            return (
                "你刚才的输出不是合法 JSON。请修复并仅输出 JSON 数组。\n"
                f"约束：深度最多 {self.depth}；一级子话题最多 {self.breadth} 个；"
                "每项包含 title, question, depth, parent_title；depth=1 的 parent_title 为空。\n"
                f"话题：{topic}\n"
                "原始输出（需要修复）：\n"
                f"{truncated}\n"
            )
        return (
            "Your previous output is invalid JSON. Fix it and output JSON array only.\n"
            f"Constraints: depth <= {self.depth}; level-1 topics <= {self.breadth}. "
            "Each item has title, question, depth, parent_title; depth=1 parent_title is null.\n"
            f"Topic: {topic}\n"
            "Original output to fix:\n"
            f"{truncated}\n"
        )
