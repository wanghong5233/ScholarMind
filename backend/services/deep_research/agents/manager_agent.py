"""Manager agent for queue orchestration and dynamic topic expansion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Optional

from service.data_structures import DynamicTopicQueue, TopicBlock, TopicStatus
from utils.language import guess_language


@dataclass
class FollowupPlan:
    """Follow-up plan item for dynamic expansion."""

    title: str
    question: str
    depth: int


class ManagerAgent:
    """Manage topic queue state and dynamic follow-up generation."""

    def __init__(
        self,
        queue: DynamicTopicQueue,
        max_depth: int,
        max_followups: int = 2,
        min_summary_chars: int = 200,
    ) -> None:
        """Initialize the manager agent.

        Args:
            queue (DynamicTopicQueue): Topic queue instance.
            max_depth (int): Maximum depth for topic expansion.
            max_followups (int): Maximum follow-ups to add per block.
            min_summary_chars (int): Minimum summary length to skip expansion.
        """

        self._queue = queue
        self._max_depth = max(1, max_depth)
        self._max_followups = max(0, max_followups)
        self._min_summary_chars = max(50, min_summary_chars)
        self._existing_titles = {
            (block.title or "").strip().lower() for block in queue.list_blocks()
        }
        self._existing_questions = {
            (block.question or "").strip().lower() for block in queue.list_blocks()
        }

    def list_blocks(self, status: Optional[TopicStatus] = None) -> List[TopicBlock]:
        """List blocks in the queue."""

        return self._queue.list_blocks(status=status)

    def mark_status(self, block_id: str, status: TopicStatus) -> None:
        """Update block status."""

        self._queue.mark_block_status(block_id, status)

    def maybe_expand(self, block: TopicBlock, summary: str, language: Optional[str]) -> List[TopicBlock]:
        """Add follow-ups if the summary indicates more depth is needed."""

        if block.followups_generated:
            return []
        block.followups_generated = True
        if block.depth >= self._max_depth or self._max_followups == 0:
            return []
        if not self._should_expand(summary):
            return []

        followups = self._build_followups(block, summary, language)
        created: List[TopicBlock] = []
        for followup in followups[: self._max_followups]:
            if self._is_duplicate(followup.title, followup.question):
                continue
            child = self._queue.add_block(
                title=followup.title,
                question=followup.question,
                depth=followup.depth,
                parent_id=block.block_id,
                max_iterations=block.max_iterations,
            )
            created.append(child)
            self._register_seen(child)
        return created

    def add_followups_from_questions(
        self,
        block: TopicBlock,
        questions: List[str],
        language: Optional[str],
    ) -> List[TopicBlock]:
        """Add follow-ups from explicit questions.

        Args:
            block (TopicBlock): Parent topic block.
            questions (List[str]): Follow-up questions from decision agent.
            language (Optional[str]): Language hint.

        Returns:
            List[TopicBlock]: Newly created topic blocks.
        """

        if not questions or self._max_followups == 0 or block.depth >= self._max_depth:
            return []
        block.followups_generated = True
        depth = min(block.depth + 1, self._max_depth)
        created: List[TopicBlock] = []
        skipped_user_clarifications = False
        for question in questions[: self._max_followups]:
            if self._looks_like_user_clarification(question):
                skipped_user_clarifications = True
                continue
            title = self._question_to_title(question, language)
            if self._is_duplicate(title, question):
                continue
            child = self._queue.add_block(
                title=title,
                question=question,
                depth=depth,
                parent_id=block.block_id,
                max_iterations=block.max_iterations,
            )
            created.append(child)
            self._register_seen(child)
        if not created and skipped_user_clarifications:
            # Decision LLM can emit user-facing clarifications; convert these into
            # autonomous follow-up tasks so queue expansion remains evidence-driven.
            fallback_plans = self._build_followups(block, summary="", language=language)
            for followup in fallback_plans[: self._max_followups]:
                if self._is_duplicate(followup.title, followup.question):
                    continue
                child = self._queue.add_block(
                    title=followup.title,
                    question=followup.question,
                    depth=depth,
                    parent_id=block.block_id,
                    max_iterations=block.max_iterations,
                )
                created.append(child)
                self._register_seen(child)
        return created

    def _should_expand(self, summary: str) -> bool:
        """Heuristic to decide whether to expand a topic block."""

        if not summary:
            return True
        if len(summary) < self._min_summary_chars:
            return True
        flags = ["insufficient", "unclear", "missing", "no answer", "无法", "不足", "不明确", "缺少"]
        lowered = summary.lower()
        return any(flag in lowered for flag in flags)

    def _build_followups(
        self,
        block: TopicBlock,
        summary: str,
        language: Optional[str],
    ) -> List[FollowupPlan]:
        """Generate follow-up topics based on a summary."""

        lang = language or guess_language(block.title or summary)
        depth = min(block.depth + 1, self._max_depth)
        if lang == "zh":
            return [
                FollowupPlan(
                    title=f"{block.title} 的关键证据与引用依据",
                    question=f"{block.title} 有哪些权威证据或论文支持？",
                    depth=depth,
                ),
                FollowupPlan(
                    title=f"{block.title} 的局限性与待解决问题",
                    question=f"{block.title} 的主要局限性和开放问题是什么？",
                    depth=depth,
                ),
            ]
        return [
            FollowupPlan(
                title=f"Evidence supporting {block.title}",
                question=f"What evidence or citations support {block.title}?",
                depth=depth,
            ),
            FollowupPlan(
                title=f"Limitations and open issues for {block.title}",
                question=f"What are the limitations or open questions for {block.title}?",
                depth=depth,
            ),
        ]

    def _is_duplicate(self, title: str, question: str) -> bool:
        """Check whether a follow-up is already in the queue."""

        title_key = (title or "").strip().lower()
        question_key = (question or "").strip().lower()
        return title_key in self._existing_titles or question_key in self._existing_questions

    def _register_seen(self, block: TopicBlock) -> None:
        """Track new block titles/questions to avoid duplicates."""

        if block.title:
            self._existing_titles.add(block.title.strip().lower())
        if block.question:
            self._existing_questions.add(block.question.strip().lower())

    def _question_to_title(self, question: str, language: Optional[str]) -> str:
        """Derive a short title from a question."""

        lang = language or guess_language(question)
        title = question.strip()
        if lang == "zh":
            return title.replace("？", "").replace("?", "")[:48]
        return title.rstrip("?")[:60]

    @staticmethod
    def _looks_like_user_clarification(question: str) -> bool:
        """Detect follow-up prompts that ask users for preferences/details."""

        normalized = " ".join(str(question or "").strip().lower().split())
        if not normalized:
            return True
        zh_markers = (
            "你希望",
            "你更想",
            "你更倾向",
            "请提供",
            "请给出",
            "你能否",
            "是否需要",
            "请在",
        )
        en_markers = (
            "can you provide",
            "could you provide",
            "would you like",
            "do you prefer",
            "what is your preference",
            "please provide",
            "please share",
        )
        if any(marker in normalized for marker in zh_markers):
            return True
        return any(marker in normalized for marker in en_markers)


class AsyncManagerAgentWrapper:
    """Async wrapper for ManagerAgent."""

    def __init__(self, manager: ManagerAgent) -> None:
        """Wrap ManagerAgent with an asyncio lock."""

        self._manager = manager
        self._lock = asyncio.Lock()

    async def list_blocks(self, status: Optional[TopicStatus] = None) -> List[TopicBlock]:
        """List blocks safely in async contexts."""

        async with self._lock:
            return list(self._manager.list_blocks(status=status))

    async def mark_status(self, block_id: str, status: TopicStatus) -> None:
        """Update block status safely in async contexts."""

        async with self._lock:
            self._manager.mark_status(block_id, status)

    async def maybe_expand(self, block: TopicBlock, summary: str, language: Optional[str]) -> List[TopicBlock]:
        """Maybe add follow-ups safely in async contexts."""

        async with self._lock:
            return self._manager.maybe_expand(block, summary, language)

    async def add_followups_from_questions(
        self,
        block: TopicBlock,
        questions: List[str],
        language: Optional[str],
    ) -> List[TopicBlock]:
        """Add follow-ups from explicit questions safely in async contexts."""

        async with self._lock:
            return self._manager.add_followups_from_questions(block, questions, language)
