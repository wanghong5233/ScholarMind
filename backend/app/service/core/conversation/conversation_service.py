"""Shared conversation context utilities."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.config import settings
from models.memory import Memory
from models.message import Message
from models.session import Session as SessionModel
from service.core.rag.history.short_term_memory import (
    ShortTermMemoryBuilder,
    ShortTermMemoryDebug,
)
from service.core.rag.llm.client import LLMClient
from service.memory_service import LongTermMemoryService
from service.session_service import SessionService


class ConversationService:
    """Provide shared conversation context utilities."""

    def __init__(
        self,
        db: Session,
        *,
        stm_builder: Optional[ShortTermMemoryBuilder] = None,
        ltm_service: Optional[LongTermMemoryService] = None,
        llm_client: Optional[LLMClient] = None,
        session_service: Optional[SessionService] = None,
    ) -> None:
        """Initialize the conversation service.

        Args:
            db (Session): Database session.
            stm_builder (Optional[ShortTermMemoryBuilder]): Optional STM builder.
            ltm_service (Optional[LongTermMemoryService]): Optional LTM service.
        """
        self.db = db
        self.stm_builder = stm_builder or ShortTermMemoryBuilder(db)
        self.ltm_service = ltm_service or LongTermMemoryService(db)
        self.llm_client = llm_client or LLMClient()
        self.session_service = session_service or SessionService(db)

    def build_history_slice(
        self,
        *,
        session_id: str,
        question: str,
    ) -> Tuple[List[Dict[str, str]], ShortTermMemoryDebug, Optional[List[float]]]:
        """Build a short-term memory slice for a session.

        Args:
            session_id (str): Session identifier.
            question (str): Query text for STM selection.

        Returns:
            Tuple[List[Dict[str, str]], ShortTermMemoryDebug, Optional[List[float]]]:
                History list, debug info, and query embedding.
        """
        return self.stm_builder.build_history(
            session_id=session_id,
            question=question,
        )

    def fetch_focus_doc_ids(
        self,
        *,
        user_id: int | str,
        session: SessionModel,
        query: str,
        query_embedding: Optional[List[float]] = None,
    ) -> Tuple[List[int], Dict[str, object]]:
        """Fetch boosted document ids from long-term memory.

        Args:
            user_id (int | str): User identifier.
            session (SessionModel): Session model.
            query (str): Query text.
            query_embedding (Optional[List[float]]): Optional query embedding.

        Returns:
            Tuple[List[int], Dict[str, object]]: Boost doc ids and debug metadata.
        """
        return self.ltm_service.fetch_focus_doc_ids(
            user_id=user_id,
            session=session,
            query=query,
            query_embedding=query_embedding,
        )

    def list_memory_profile(
        self,
        *,
        user_id: int | str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """List long-term memory items for a user.

        Args:
            user_id (int | str): User identifier.
            limit (int): Max number of memory items.

        Returns:
            List[Dict[str, Any]]: Memory entries.
        """
        memories = (
            self.db.query(Memory)
            .filter(Memory.user_id == str(user_id), Memory.status == "active")
            .order_by(Memory.last_accessed.desc(), Memory.updated_at.desc())
            .limit(limit)
            .all()
        )

        items: List[Dict[str, Any]] = []
        for memory in memories:
            items.append(
                {
                    "memory_id": str(memory.memory_id),
                    "content": memory.content,
                    "summary": memory.summary,
                    "document_id": memory.document_id,
                    "memory_type": memory.memory_type,
                    "importance": memory.importance,
                    "confidence": memory.confidence,
                    "access_count": memory.access_count,
                    "last_accessed": memory.last_accessed.isoformat()
                    if memory.last_accessed
                    else None,
                    "created_at": memory.created_at.isoformat()
                    if memory.created_at
                    else None,
                    "meta_data": memory.meta_data,
                }
            )
        return items

    def maybe_update_rolling_summary(
        self,
        *,
        session_id: str,
        min_messages: Optional[int] = None,
        every_messages: Optional[int] = None,
        history_scan: Optional[int] = None,
    ) -> Optional[str]:
        """Update rolling summary if the session crosses thresholds.

        Args:
            session_id (str): Session identifier.
            min_messages (Optional[int]): Minimum messages to trigger summary.
            every_messages (Optional[int]): Interval to trigger summary refresh.
            history_scan (Optional[int]): Max messages to scan for summary input.

        Returns:
            Optional[str]: Updated rolling summary if computed.
        """
        if not getattr(settings, "ENABLE_ROLLING_SUMMARY", True):
            return None

        base_turns = max(int(getattr(settings, "SM_HISTORY_MAX_TURNS", 8) or 8), 1)
        min_messages = min_messages or max(base_turns * 2, base_turns)
        every_messages = every_messages or base_turns
        history_scan = history_scan or max(int(getattr(settings, "SM_STM_SCAN_MESSAGES", 40) or 40), 1)

        total_messages = (
            self.db.query(Message)
            .filter(Message.session_id == session_id)
            .count()
        )
        if total_messages < min_messages or total_messages % every_messages != 0:
            return None

        session_obj = self.session_service.get_session_by_id(session_id=session_id)
        if not session_obj:
            return None

        history = self._build_history_turns(session_id=session_id, limit=history_scan)
        if not history:
            return None
        if session_obj.rolling_summary:
            history = [
                {"role": "system", "content": f"[rolling_summary]\n{session_obj.rolling_summary}"}
            ] + history

        summary = self._summarize_history(history)
        if summary:
            self.session_service.update_rolling_summary(
                session_id=session_id,
                rolling_summary=summary,
            )
        return summary

    def _build_history_turns(self, *, session_id: str, limit: int) -> List[Dict[str, str]]:
        """Build a chronological list of conversation turns for summarization."""
        messages = (
            self.db.query(Message)
            .filter(Message.session_id == session_id)
            .order_by(Message.create_time.desc())
            .limit(limit)
            .all()
        )
        messages.reverse()
        history: List[Dict[str, str]] = []
        for msg in messages:
            if msg.user_question:
                history.append({"role": "user", "content": msg.user_question})
            if msg.model_answer:
                history.append({"role": "assistant", "content": msg.model_answer})
        return history

    def _summarize_history(self, history: List[Dict[str, str]]) -> str:
        """Summarize history into a compact rolling summary."""
        if not history:
            return ""
        lines = []
        for msg in history:
            role = msg.get("role", "user")
            content = str(msg.get("content", ""))
            lines.append(f"{role}: {content}")
        body = "\n".join(lines[-20:])
        if getattr(settings, "SM_DEFAULT_LANGUAGE", "zh") == "zh":
            system_prompt = (
                "请将以下对话历史压缩为6-10条要点，务必保留：用户目标/约束、偏好、拒答规则、安全要求、"
                "已达成结论与未决问题，以及与当前问题相关的关键信息。不要虚构。"
            )
        else:
            system_prompt = (
                "Summarize the conversation into 6-10 bullet points. MUST preserve: user goals/constraints, "
                "preferences, refusal/safety rules, reached conclusions and open questions, and key facts "
                "relevant to the current query. Do not fabricate."
            )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": body},
        ]
        try:
            summary = self.llm_client.generate(messages, temperature=0.2, max_tokens=256, stream=False)
        except Exception:
            summary = ""
        if not summary:
            try:
                summary = self.llm_client.generate(messages, temperature=0.2, max_tokens=256, stream=False)
            except Exception:
                summary = ""
        return summary or ""

    def build_context_pack(
        self,
        *,
        session_id: str,
        user_id: int | str,
        question: str,
        memory_limit: int = 10,
        history_limit: int = 8,
        memory_preview_limit: int = 8,
        max_text_chars: int = 2000,
        max_context_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build a unified context pack for downstream services.

        Args:
            session_id (str): Session identifier.
            user_id (int | str): User identifier.
            question (str): User query text.
            memory_limit (int): Max memory items to fetch.
            history_limit (int): Max history turns to include in preview.
            memory_preview_limit (int): Max memory items to include in preview.
            max_text_chars (int): Max characters for the formatted text block.
            max_context_tokens (Optional[int]): Max tokens for the formatted text block.

        Returns:
            Dict[str, Any]: Context pack with history, memory, and formatted text.
        """
        history, debug, _ = self.build_history_slice(
            session_id=session_id,
            question=question,
        )
        session_obj = self.session_service.get_session_by_id(session_id=session_id)
        rolling_summary = session_obj.rolling_summary if session_obj else None
        memory_items = self.list_memory_profile(
            user_id=user_id,
            limit=memory_limit,
        )
        context_text = self._format_context_text(
            history=history,
            memory_items=memory_items,
            question=question,
            rolling_summary=rolling_summary,
            history_limit=history_limit,
            memory_limit=memory_preview_limit,
            max_chars=max_text_chars,
            max_tokens=max_context_tokens,
        )
        context_tokens = self._estimate_tokens(context_text)
        return {
            "history": history,
            "debug": debug,
            "memory_items": memory_items,
            "context_text": context_text,
            "rolling_summary": rolling_summary,
            "context_meta": {
                "context_tokens": context_tokens,
                "context_max_tokens": max_context_tokens,
                "context_max_chars": max_text_chars,
                "history_count": len(history),
                "memory_count": len(memory_items),
                "rolling_summary_present": bool(rolling_summary),
            },
        }

    def _format_context_text(
        self,
        *,
        history: List[Dict[str, str]],
        memory_items: List[Dict[str, Any]],
        question: str,
        rolling_summary: Optional[str],
        history_limit: int,
        memory_limit: int,
        max_chars: int,
        max_tokens: Optional[int],
    ) -> str:
        """Format a compact context text block for LLM injection.

        Args:
            history (List[Dict[str, str]]): STM history turns.
            memory_items (List[Dict[str, Any]]): LTM memory entries.
            question (str): User query text.
            rolling_summary (Optional[str]): Rolling summary text.
            history_limit (int): Max history turns to include.
            memory_limit (int): Max memory items to include.
            max_chars (int): Max characters for the formatted text block.
            max_tokens (Optional[int]): Max tokens for the formatted text block.

        Returns:
            str: Formatted context text.
        """
        def _truncate(text: str, limit: int) -> str:
            text = (text or "").strip()
            if len(text) <= limit:
                return text
            return f"{text[:limit]}..."

        language = (getattr(settings, "SM_DEFAULT_LANGUAGE", "zh") or "zh").lower()
        if language == "zh":
            lines: List[str] = ["[对话上下文]"]
        else:
            lines = ["[Conversation Context]"]
        if question:
            label = "问题" if language == "zh" else "Question"
            lines.append(f"{label}: {_truncate(question, 200)}")

        if rolling_summary:
            label = "滚动摘要" if language == "zh" else "Rolling Summary"
            lines.append(f"{label}:")
            lines.append(f"- {_truncate(rolling_summary, 300)}")

        if history:
            label = "相关历史" if language == "zh" else "Relevant History"
            lines.append(f"{label}:")
            for item in history[-history_limit:]:
                role = (item.get("role") or "user").strip()
                content = _truncate(str(item.get("content") or ""), 240)
                if content:
                    lines.append(f"- {role}: {content}")

        if memory_items:
            label = "用户记忆（LTM）" if language == "zh" else "User Memory (LTM)"
            lines.append(f"{label}:")
            for mem in memory_items[:memory_limit]:
                summary = mem.get("summary") or mem.get("content") or ""
                summary = _truncate(str(summary), 200)
                if summary:
                    lines.append(f"- {summary}")

        text = "\n".join(lines).strip()
        if max_tokens and max_tokens > 0:
            text = self._trim_text_to_tokens(text, max_tokens=max_tokens)
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "..."
        return text

    def _trim_text_to_tokens(self, text: str, *, max_tokens: int) -> str:
        """Trim text to the approximate token budget."""
        if not text:
            return ""
        tokens = self._estimate_tokens(text)
        if tokens <= max_tokens:
            return text
        ratio = max_tokens / max(tokens, 1)
        cut = max(int(len(text) * ratio), 200)
        return text[:cut].rstrip() + "..."

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for a text string."""
        try:
            import tiktoken  # type: ignore

            model = None
            if getattr(settings, "SM_LLM_TYPE", "openai") == "openai":
                model = getattr(settings, "OPENAI_MODEL_NAME", None)
            enc = None
            if model:
                try:
                    enc = tiktoken.encoding_for_model(model)
                except Exception:
                    enc = tiktoken.get_encoding("cl100k_base")
            else:
                enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text or ""))
        except Exception:
            if not text:
                return 0
            zh = sum(1 for c in text if ord(c) > 127)
            en = len(text) - zh
            return zh + en // 4
