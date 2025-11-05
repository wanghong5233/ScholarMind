from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from core.config import settings
from models.memory import Memory
from models.session import Session as SessionModel
from service.core.rag.nlp.model import generate_embedding
from service.session_service import SessionService


class LongTermMemoryService:
    """管理 LTM（长期记忆）的读写，负责记忆引导检索的自适应控制。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.session_service = SessionService(db)
        self.logger = logging.getLogger("rag.memory.ltm")

    # ------------------------------------------------------------------
    # Public APIs
    # ------------------------------------------------------------------
    def fetch_focus_doc_ids(
        self,
        *,
        user_id: int | str,
        session: SessionModel,
        query: str,
        query_embedding: Optional[List[float]] = None,
    ) -> Tuple[List[int], Dict[str, object]]:
        if session.memory_guide_disabled:
            try:
                self.logger.debug(
                    "LTM.fetch_focus_doc_ids user=%s session=%s disabled fail_count=%s",
                    user_id,
                    session.session_id,
                    session.memory_guide_fail_count,
                )
            except Exception:
                pass
            return [], {
                "disabled": True,
                "reason": "auto_disabled",
                "fail_count": session.memory_guide_fail_count or 0,
            }

        candidates = (
            self.db.query(Memory)
            .filter(Memory.user_id == str(user_id), Memory.status == "active")
            .order_by(Memory.last_accessed.desc().nullslast(), Memory.created_at.desc())
            .limit(int(getattr(settings, "SM_LTM_MAX_CANDIDATES", 32) or 32))
            .all()
        )

        if not candidates:
            return [], {
                "disabled": False,
                "fail_count": session.memory_guide_fail_count or 0,
                "candidates": [],
            }

        if query_embedding is None:
            query_embedding = self._compute_embedding(query)

        lambda_decay = float(getattr(settings, "SM_LTM_SCORE_DECAY_LAMBDA", 0.01) or 0.01)
        semantic_weight = float(getattr(settings, "SM_LTM_SEMANTIC_WEIGHT", 0.75) or 0.75)
        time_weight = float(getattr(settings, "SM_LTM_TIME_WEIGHT", 0.25) or 0.25)

        now = datetime.now(timezone.utc)
        scored_docs: Dict[str, Tuple[float, Dict[str, object]]] = {}
        embedding_updated = False

        for memory in candidates:
            if not memory.document_id:
                continue
            mem_embedding = self._ensure_memory_embedding(memory)
            if mem_embedding is None and query_embedding is None:
                continue

            semantic = 0.0
            if query_embedding and mem_embedding:
                semantic = max(self._cosine_similarity(query_embedding, mem_embedding), 0.0)

            mem_time = memory.last_accessed or memory.updated_at or memory.created_at or now
            if mem_time.tzinfo is None:
                mem_time = mem_time.replace(tzinfo=timezone.utc)
            delta_hours = max((now - mem_time).total_seconds() / 3600.0, 0.0)
            time_score = math.exp(-lambda_decay * delta_hours)

            score = semantic_weight * semantic + time_weight * time_score

            existing = scored_docs.get(memory.document_id)
            if not existing or score > existing[0]:
                scored_docs[memory.document_id] = (
                    score,
                    {
                        "memory_id": str(memory.memory_id),
                        "score": round(score, 4),
                        "semantic": round(semantic, 4),
                        "time": round(time_score, 4),
                        "type": memory.memory_type,
                        "confidence": memory.confidence,
                        "importance": memory.importance,
                        "access_count": memory.access_count,
                    },
                )
            if mem_embedding is None:
                embedding_updated = True

        if embedding_updated:
            try:
                self.db.flush()
            except Exception as exc:
                self.logger.debug("LTM flush failed: %s", exc)

        if not scored_docs:
            return [], {
                "disabled": False,
                "fail_count": session.memory_guide_fail_count or 0,
                "candidates": [],
            }

        sorted_docs = sorted(scored_docs.items(), key=lambda item: item[1][0], reverse=True)
        max_docs = max(int(getattr(settings, "SM_LTM_MAX_DOCIDS", 2) or 2), 0)
        selected_docs = [item[0] for item in sorted_docs[:max_docs]]

        # 更新访问计数 & last_accessed（仅对被选中的记忆）
        now_time = datetime.now(timezone.utc)
        for doc_id, _ in sorted_docs[:max_docs]:
            memory = (
                self.db.query(Memory)
                .filter(
                    Memory.user_id == str(user_id),
                    Memory.document_id == doc_id,
                    Memory.status == "active",
                )
                .first()
            )
            if memory:
                memory.last_accessed = now_time
                memory.access_count = (memory.access_count or 0) + 1

        debug = {
            "disabled": False,
            "fail_count": session.memory_guide_fail_count or 0,
            "candidates": [info for _, info in sorted_docs],
            "selected": selected_docs,
        }

        try:
            self.logger.debug(
                "LTM.fetch_focus_doc_ids user=%s session=%s candidates=%s selected=%s",
                user_id,
                session.session_id,
                len(sorted_docs),
                selected_docs,
            )
        except Exception:
            pass

        return [int(doc) for doc in selected_docs if str(doc).isdigit()], debug

    def record_memories(
        self,
        *,
        user_id: int | str,
        session_id: str,
        question: str,
        citations: Sequence[Dict[str, object]],
    ) -> None:
        if not citations:
            return

        now = datetime.now(timezone.utc)
        for citation in citations:
            doc_id = citation.get("document_id")
            if doc_id in (None, ""):
                continue
            doc_id_str = str(doc_id)
            memory = (
                self.db.query(Memory)
                .filter(Memory.user_id == str(user_id), Memory.document_id == doc_id_str)
                .first()
            )
            content = f"用户在 session {session_id} 的问答中引用了文档 {doc_id_str}：{question[:160]}"
            meta_data = {
                "document_id": doc_id_str,
                "session_id": session_id,
                "page": citation.get("page"),
                "section": citation.get("section"),
            }

            if memory:
                memory.content = content
                memory.meta_data = meta_data
                memory.confidence = min(1.0, (memory.confidence or 0.6) + 0.05)
                memory.importance = min(1.0, (memory.importance or 0.5) + 0.05)
                memory.updated_at = now
                memory.status = "active"
                try:
                    self.logger.debug(
                        "LTM.record_memories update user=%s doc=%s confidence=%.3f importance=%.3f",
                        user_id,
                        doc_id_str,
                        memory.confidence,
                        memory.importance,
                    )
                except Exception:
                    pass
                continue

            embedding = self._compute_embedding(content)
            new_memory = Memory(
                user_id=str(user_id),
                session_id=session_id,
                memory_type="episodic",
                content=content,
                document_id=doc_id_str,
                meta_data=meta_data,
                importance=0.6,
                confidence=0.6,
                embedding=embedding,
                summary=self._build_summary(content, 200),
                access_count=0,
                last_accessed=None,
            )
            self.db.add(new_memory)
            try:
                self.logger.debug(
                    "LTM.record_memories insert user=%s doc=%s embedding=%s",
                    user_id,
                    doc_id_str,
                    bool(embedding),
                )
            except Exception:
                pass

    def record_guided_result(self, *, session_id: str, success: bool) -> Dict[str, object]:
        threshold = int(getattr(settings, "SM_MEMORY_GUIDE_MAX_FAILS", 5) or 5)
        if success:
            self.session_service.reset_memory_guide(session_id=session_id)
            result = {"success": True, "auto_disabled": False}
        else:
            disabled = self.session_service.increment_memory_guide_fail(session_id=session_id, threshold=threshold)
            result = {"success": False, "auto_disabled": bool(disabled)}

        try:
            session_state = self.session_service.get_session_by_id(session_id=session_id)
            fail_count = session_state.memory_guide_fail_count if session_state else None
            self.logger.debug(
                "LTM.record_guided_result session=%s success=%s fail_count=%s auto_disabled=%s",
                session_id,
                success,
                fail_count,
                result.get("auto_disabled"),
            )
        except Exception:
            pass

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _compute_embedding(self, text: str) -> Optional[List[float]]:
        text = (text or "").strip()
        if not text:
            return None
        try:
            vecs = generate_embedding([text])
            if vecs and vecs[0] is not None:
                return list(vecs[0])
        except Exception as exc:
            self.logger.debug("LTM embedding failed: %s", exc)
        return None

    def _ensure_memory_embedding(self, memory: Memory) -> Optional[List[float]]:
        if memory.embedding is not None:
            return list(memory.embedding)
        if not memory.content:
            return None
        embedding = self._compute_embedding(memory.content)
        if embedding is not None:
            memory.embedding = embedding
        return embedding

    def _build_summary(self, text: str, max_chars: int) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if len(text) <= max_chars:
            return text
        return text[:max_chars]

    def _cosine_similarity(self, a: Sequence[float], b: Sequence[float]) -> float:
        if not a or not b:
            return 0.0
        length = min(len(a), len(b))
        if length == 0:
            return 0.0
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for i in range(length):
            av = float(a[i])
            bv = float(b[i])
            dot += av * bv
            norm_a += av * av
            norm_b += bv * bv
        if norm_a <= 0 or norm_b <= 0:
            return 0.0
        return dot / math.sqrt(norm_a * norm_b)


