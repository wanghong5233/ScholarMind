"""Document question generation utilities."""

from __future__ import annotations

from typing import List
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.config import settings
from exceptions.base import PermissionDeniedException, ResourceNotFoundException
from models.user import User
from schemas.document import CriticalQuestionsResponse
from service import knowledgebase_service, document_service
from service.core.conversation.chat_generation_service import ChatGenerationService
from service.core.rag.retriever import RAGRetriever
from service.core.rag.service import RAGService
from service.core.rag.graph.graph_service import KnowledgeGraphService
from service.core.rag.providers.registry import resolve_provider
from service.core.rag.utils.retrieval_stats import build_provider_stats
from utils.ask_logger import AskEventLogger
from utils.quota import quota
from utils.rate_limiter import rate_limiter


class DocumentQuestionService:
    """Generate critical questions for a document."""

    def __init__(self, *, db: Session, current_user: User) -> None:
        """Initialize document question service.

        Args:
            db (Session): SQLAlchemy session.
            current_user (User): Authenticated user.
        """
        self.db = db
        self.current_user = current_user
        self.rag = RAGService()
        self.chat_service = ChatGenerationService()
        self.retriever = RAGRetriever(self.rag)
        self.graph_service = KnowledgeGraphService(db=db)

    def generate_critical_questions(
        self,
        *,
        kb_id: int,
        doc_id: int,
        top_n: int = 6,
    ) -> CriticalQuestionsResponse:
        """Generate critical questions for a document.

        Args:
            kb_id (int): Knowledge base id.
            doc_id (int): Document id.
            top_n (int): Number of questions.

        Returns:
            CriticalQuestionsResponse: Generated questions with citations.
        """
        try:
            kb = knowledgebase_service.get_kb_by_id(
                db=self.db, kb_id=kb_id, user_id=self.current_user.id
            )
        except (ResourceNotFoundException, PermissionDeniedException) as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        try:
            document_service.get_document_by_id(
                db=self.db, doc_id=doc_id, user_id=self.current_user.id, kb_id=kb_id
            )
        except (ResourceNotFoundException, PermissionDeniedException) as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        bucket = f"criticalq:{self.current_user.id}:{kb_id}:{doc_id}"
        if not rate_limiter.check_and_consume(bucket, limit=60, window_seconds=60):
            raise HTTPException(status_code=429, detail="Too Many Requests")
        day_key = f"criticalq:day:{self.current_user.id}:{int(__import__('time').time())//86400}"
        if not quota.consume_count(day_key, settings.DAILY_ASK_COUNT, window_seconds=86400):
            raise HTTPException(status_code=429, detail="Daily quota exceeded")

        dims = [
            "核心问题与动机",
            "关键方法与假设",
            "实验设计与数据",
            "主要结论与局限",
            "与相关工作比较",
            "潜在扩展与开放问题",
        ]
        count = max(1, min(int(top_n), len(dims)))
        question_intro = (
            "请基于以下论文内容，面向深入阅读提出"
            + str(count)
            + "个批判性问题，要求具体、可操作，避免泛泛而谈。"
        )
        query = "; ".join(dims[:count])
        provider_name = resolve_provider(getattr(kb, "rag_provider", None))
        rag_config = getattr(kb, "rag_config", None)
        graph_boost_ids, graph_boost_chunks, graph_query_variants, graph_debug = (
            self.graph_service.suggest_boost_doc_ids(
            kb_id=kb_id,
            query=query,
            provider=provider_name,
            rag_config=rag_config,
            )
        )
        chunks = self.retriever.retrieve(
            query=query,
            kb_id=kb_id,
            top_k=max(8, count * 4),
            focus_doc_ids=[doc_id],
            boost_doc_ids=graph_boost_ids or None,
            boost_chunk_ids=graph_boost_chunks or None,
            index_override=None,
            provider=provider_name,
            extra_variants=graph_query_variants or None,
        )
        for chunk in chunks:
            metadata = chunk.setdefault("metadata", {})
            metadata["rag_provider"] = provider_name
        provider_stats = build_provider_stats(chunks)
        prompt = (
            question_intro + "\n请按序号给出：1) 问题描述 2) 指向性提示（可引用 [文档ID:页码]）。"
        )
        content = self.chat_service.generate(
            question=prompt,
            chunks=chunks,
            stream=False,
            history=[],
            compress_history=False,
        )
        text = (content or "").strip()
        lines = [x.strip(" -•\t").strip() for x in text.splitlines() if x.strip()]
        if len(lines) <= 2:
            parts = re.split(r"(?:^|\n)\s*(?:\d+\.|\d+、|\(\d+\))\s*", text)
            lines = [p.strip() for p in parts if p and p.strip()]
        questions = lines[:count]
        citations = self.rag.build_citations(chunks)
        debug = {
            "doc_id": doc_id,
            "dims": dims[:count],
            "retrieval": self.retriever.get_last_retrieval_debug() or {},
            "graph": graph_debug,
            "provider_stats": provider_stats,
        }
        try:
            AskEventLogger().log_event(
                {
                    "user_id": str(self.current_user.id),
                    "session_id": None,
                    "kb_id": int(kb_id),
                    "question": prompt[:512],
                    "top_k": len(chunks),
                    "strategy": getattr(settings, "SM_RETRIEVAL_STRATEGY", "multi_stage"),
                    "hits": len(chunks),
                    "retrieval": self.retriever.get_last_retrieval_debug() or {},
                    "provider_stats": provider_stats,
                    "graph": graph_debug,
                    "citations": citations,
                    "usage": self.chat_service.get_last_usage() or {},
                    "answer_chars": len(content or ""),
                    "variant": "critical_questions",
                }
            )
        except Exception:
            pass

        return CriticalQuestionsResponse(
            questions=questions,
            citations=citations,
            debug=debug,
        )
