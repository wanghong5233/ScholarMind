"""Long-term memory (LTM) layer — extract, store, and recall user facts via Elasticsearch.

This module is designed for **minimal invasiveness**: it introduces no new database
tables, no new infrastructure dependencies (reuses the existing ES cluster and
embedding pipeline), and can be feature-flagged off at any time via the
``SM_LTM_ENABLED`` setting.

Architecture
~~~~~~~~~~~~
1. **FactExtractor** — uses the existing ``LLMClient`` to pull structured facts
   from a completed Q&A turn (runs asynchronously after response delivery).
2. **LongTermMemoryStore** — thin wrapper around ``ESConnection`` that manages a
   dedicated ``sm_ltm_facts`` index for per-user fact documents.
3. **LongTermMemoryRecaller** — at query time, embeds the user question and
   performs a filtered kNN search in the facts index to retrieve relevant memories,
   returning them as a short system-prompt segment.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from core.config import settings
from service.core.rag.nlp.model import generate_embedding
from service.core.rag.utils.es_conn import ESConnection

logger = logging.getLogger("rag.history.ltm")

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

_INDEX_NAME = "sm_ltm_facts"

_INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
    "mappings": {
        "properties": {
            "fact_id": {"type": "keyword"},
            "user_id": {"type": "keyword"},
            "session_id": {"type": "keyword"},
            "fact": {"type": "text", "analyzer": "standard"},
            "category": {"type": "keyword"},
            "embedding": {
                "type": "dense_vector",
                "dims": int(getattr(settings, "SM_EMBEDDING_DIMENSIONS", 1024) or 1024),
                "index": True,
                "similarity": "cosine",
            },
            "importance": {"type": "float"},
            "access_count": {"type": "integer"},
            "created_at": {"type": "date"},
            "updated_at": {"type": "date"},
        }
    },
}


def _ltm_enabled() -> bool:
    raw = getattr(settings, "SM_LTM_ENABLED", False)
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes")
    return bool(raw)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExtractedFact:
    fact: str
    category: str = "general"
    importance: float = 0.5


@dataclass
class StoredFact:
    fact_id: str
    user_id: str
    session_id: str
    fact: str
    category: str
    importance: float
    access_count: int
    created_at: str
    updated_at: str
    score: float = 0.0


@dataclass
class LTMRecallDebug:
    enabled: bool
    facts_retrieved: int
    elapsed_ms: int
    details: List[Dict[str, object]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. FactExtractor — LLM-based fact extraction
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT_ZH = """\
你是一个信息提取助手。请从以下用户与AI的一轮对话中，提取出关于**用户**的关键事实信息。

规则：
- 只提取关于用户本人的事实（偏好、身份、研究方向、习惯、需求等）
- 不要提取通用知识或AI回答中的常识
- 如果对话中没有关于用户的有价值信息，返回空数组 []
- 每条事实应该简洁、独立、有信息量
- category 可选值：research_interest, preference, identity, skill, need, other

请以 JSON 数组格式返回，每个元素包含 fact 和 category 字段。
示例：[{"fact": "用户正在研究RAG系统的优化", "category": "research_interest"}]

对话内容：
用户问题：{question}
AI回答（摘要）：{answer_summary}

请提取事实（仅返回JSON数组，不要其他文字）："""

_EXTRACT_PROMPT_EN = """\
You are an information extraction assistant. Extract key facts about the **user** \
from the following Q&A turn.

Rules:
- Only extract facts about the user (preferences, identity, research interests, etc.)
- Do NOT extract general knowledge from the AI answer
- If no valuable user facts exist, return an empty array []
- Each fact should be concise, self-contained, and informative
- category values: research_interest, preference, identity, skill, need, other

Return a JSON array where each element has "fact" and "category" fields.
Example: [{{"fact": "User is researching RAG optimization", "category": "research_interest"}}]

Conversation:
User question: {question}
AI answer (summary): {answer_summary}

Extract facts (return JSON array only):"""


class FactExtractor:
    """Extract user-related facts from a completed conversation turn."""

    def __init__(self, *, language: str = "zh") -> None:
        self.language = language

    def extract(
        self,
        *,
        question: str,
        answer: str,
        max_answer_chars: int = 500,
    ) -> List[ExtractedFact]:
        if not _ltm_enabled():
            return []
        if not question or not question.strip():
            return []

        from service.core.rag.llm.client import LLMClient

        llm = LLMClient(task="aux")
        answer_summary = (answer or "")[:max_answer_chars]
        template = _EXTRACT_PROMPT_ZH if self.language == "zh" else _EXTRACT_PROMPT_EN
        prompt = template.format(question=question, answer_summary=answer_summary)

        try:
            raw = llm.generate(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=512,
                stream=False,
            )
            raw_text = raw if isinstance(raw, str) else "".join(raw)
        except Exception as exc:
            logger.warning("LTM fact extraction LLM call failed: %s", exc)
            return []

        return self._parse_facts(raw_text)

    @staticmethod
    def _parse_facts(raw_text: str) -> List[ExtractedFact]:
        text = raw_text.strip()
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            items = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            logger.debug("LTM fact extraction JSON parse failed: %s", text[:200])
            return []
        facts: List[ExtractedFact] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            fact_text = str(item.get("fact", "")).strip()
            if not fact_text or len(fact_text) < 4:
                continue
            category = str(item.get("category", "other")).strip() or "other"
            importance = float(item.get("importance", 0.5) or 0.5)
            facts.append(ExtractedFact(fact=fact_text, category=category, importance=importance))
        return facts


# ---------------------------------------------------------------------------
# 2. LongTermMemoryStore — ES-backed storage
# ---------------------------------------------------------------------------

class LongTermMemoryStore:
    """Manage per-user fact documents in a dedicated Elasticsearch index."""

    def __init__(self) -> None:
        self._es: Optional[ESConnection] = None
        self._index_ensured = False

    @property
    def es(self) -> ESConnection:
        if self._es is None:
            self._es = ESConnection()
        return self._es

    def _ensure_index(self) -> None:
        if self._index_ensured:
            return
        try:
            if not self.es.es.indices.exists(index=_INDEX_NAME):
                self.es.es.indices.create(index=_INDEX_NAME, body=_INDEX_MAPPING)
                logger.info("Created LTM index '%s'.", _INDEX_NAME)
        except Exception as exc:
            logger.warning("LTM index creation check failed (non-fatal): %s", exc)
        self._index_ensured = True

    # -- write ---------------------------------------------------------------

    def store_facts(
        self,
        *,
        user_id: str,
        session_id: str,
        facts: List[ExtractedFact],
        dedup_threshold: float = 0.92,
    ) -> int:
        if not _ltm_enabled() or not facts:
            return 0
        self._ensure_index()
        stored = 0
        for fact_obj in facts:
            embedding = generate_embedding(fact_obj.fact)
            if embedding is None:
                continue
            if self._is_duplicate(user_id=user_id, embedding=embedding, threshold=dedup_threshold):
                continue
            now_iso = datetime.now(timezone.utc).isoformat()
            doc = {
                "id": str(uuid.uuid4()),
                "fact_id": str(uuid.uuid4()),
                "user_id": str(user_id),
                "session_id": str(session_id),
                "fact": fact_obj.fact,
                "category": fact_obj.category,
                "embedding": embedding,
                "importance": fact_obj.importance,
                "access_count": 0,
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            errors = self.es.insert([doc], _INDEX_NAME)
            if not errors:
                stored += 1
            else:
                logger.warning("LTM fact insert error: %s", errors)
        return stored

    def _is_duplicate(
        self, *, user_id: str, embedding: List[float], threshold: float
    ) -> bool:
        try:
            body = {
                "size": 1,
                "query": {
                    "bool": {
                        "filter": [{"term": {"user_id": str(user_id)}}],
                    }
                },
                "knn": {
                    "field": "embedding",
                    "query_vector": embedding,
                    "k": 1,
                    "num_candidates": 10,
                    "filter": {"term": {"user_id": str(user_id)}},
                },
            }
            res = self.es.es.search(index=_INDEX_NAME, body=body, timeout="5s")
            hits = res.get("hits", {}).get("hits", [])
            if hits and hits[0].get("_score", 0) >= threshold:
                return True
        except Exception as exc:
            logger.debug("LTM dedup check failed (non-fatal): %s", exc)
        return False

    # -- read ----------------------------------------------------------------

    def recall(
        self,
        *,
        user_id: str,
        query_embedding: List[float],
        top_k: int = 3,
        min_score: float = 0.0,
    ) -> List[StoredFact]:
        if not _ltm_enabled():
            return []
        self._ensure_index()
        try:
            body = {
                "size": top_k,
                "knn": {
                    "field": "embedding",
                    "query_vector": query_embedding,
                    "k": top_k,
                    "num_candidates": max(top_k * 4, 20),
                    "filter": {"term": {"user_id": str(user_id)}},
                },
            }
            res = self.es.es.search(index=_INDEX_NAME, body=body, timeout="5s")
            hits = res.get("hits", {}).get("hits", [])
        except Exception as exc:
            logger.warning("LTM recall search failed: %s", exc)
            return []

        results: List[StoredFact] = []
        for hit in hits:
            score = float(hit.get("_score", 0))
            if score < min_score:
                continue
            src = hit.get("_source", {})
            results.append(StoredFact(
                fact_id=src.get("fact_id", ""),
                user_id=src.get("user_id", ""),
                session_id=src.get("session_id", ""),
                fact=src.get("fact", ""),
                category=src.get("category", "other"),
                importance=float(src.get("importance", 0.5)),
                access_count=int(src.get("access_count", 0)),
                created_at=src.get("created_at", ""),
                updated_at=src.get("updated_at", ""),
                score=score,
            ))
        return results


# ---------------------------------------------------------------------------
# 3. LongTermMemoryRecaller — query-time facade
# ---------------------------------------------------------------------------

class LongTermMemoryRecaller:
    """Query-time entry point: embed question → recall facts → format prompt segment."""

    def __init__(self, store: Optional[LongTermMemoryStore] = None) -> None:
        self._store = store or LongTermMemoryStore()

    def recall_for_prompt(
        self,
        *,
        user_id: str,
        question: str,
        query_embedding: Optional[List[float]] = None,
        top_k: int = 3,
        language: str = "zh",
    ) -> tuple[Optional[str], LTMRecallDebug]:
        t0 = time.time()
        if not _ltm_enabled():
            return None, LTMRecallDebug(enabled=False, facts_retrieved=0, elapsed_ms=0)

        top_k = max(1, int(getattr(settings, "SM_LTM_RECALL_TOP_K", top_k) or top_k))
        min_score = float(getattr(settings, "SM_LTM_RECALL_MIN_SCORE", 0.35) or 0.35)

        if query_embedding is None:
            query_embedding = generate_embedding(question)
        if query_embedding is None:
            elapsed = int((time.time() - t0) * 1000)
            return None, LTMRecallDebug(enabled=True, facts_retrieved=0, elapsed_ms=elapsed)

        facts = self._store.recall(
            user_id=str(user_id),
            query_embedding=query_embedding,
            top_k=top_k,
            min_score=min_score,
        )
        elapsed = int((time.time() - t0) * 1000)

        if not facts:
            return None, LTMRecallDebug(enabled=True, facts_retrieved=0, elapsed_ms=elapsed)

        details = [
            {
                "fact": f.fact,
                "category": f.category,
                "score": round(f.score, 4),
                "importance": f.importance,
            }
            for f in facts
        ]
        debug = LTMRecallDebug(
            enabled=True,
            facts_retrieved=len(facts),
            elapsed_ms=elapsed,
            details=details,
        )

        if language == "zh":
            header = "已知用户信息（来自历史交互记录，可用于个性化回答）："
        else:
            header = "Known user information (from past interactions, use for personalization):"
        lines = [f"- {f.fact}" for f in facts]
        segment = header + "\n" + "\n".join(lines)

        try:
            logger.debug(
                "LTM.recall user=%s facts=%s elapsed_ms=%s",
                user_id, len(facts), elapsed,
            )
        except Exception:
            pass

        return segment, debug
