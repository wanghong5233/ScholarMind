"""Chat compare orchestration service."""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.config import settings
from models.session import Session as SessionModel
from models.user import User
from schemas.knowledge_base import KnowledgeBaseCreate
from schemas.session import CompareRequest, CompareResponse
from service import document_service as document_service
from service.core.conversation.ask_utils import normalize_top_k
from service.core.conversation.chat_generation_service import ChatGenerationService
from service.core.rag.retriever import RAGRetriever
from service.core.rag.service import RAGService
from service.core.rag.graph.graph_service import KnowledgeGraphService
from service.core.rag.providers.registry import resolve_provider
from service.core.rag.utils.retrieval_stats import build_provider_stats
from service import knowledgebase_service
from service.session_service import SessionService
from utils.ask_logger import AskEventLogger


class ChatCompareOrchestrator:
    """Orchestrate document comparison within a session."""

    def __init__(self, *, db: Session, current_user: User) -> None:
        """Initialize the compare orchestrator.

        Args:
            db (Session): SQLAlchemy session.
            current_user (User): Authenticated user.
        """
        self.db = db
        self.current_user = current_user
        self.session_service = SessionService(db)
        self.rag = RAGService()
        self.chat_service = ChatGenerationService()
        self.retriever = RAGRetriever(self.rag)
        self.graph_service = KnowledgeGraphService(db=db)

    def handle(self, *, session_id: str, payload: CompareRequest) -> CompareResponse:
        """Handle compare request.

        Args:
            session_id (str): Session identifier.
            payload (CompareRequest): Compare payload.

        Returns:
            CompareResponse: Compare output.
        """
        s = self._get_session(session_id)
        if not s.knowledge_base_id:
            raise HTTPException(status_code=400, detail="该会话未绑定知识库")

        top_k: Optional[int] = None
        if s.defaults_json:
            try:
                data = json.loads(s.defaults_json)
                if isinstance(data.get("topK"), int):
                    top_k = data.get("topK")
            except Exception:
                pass
        top_k = normalize_top_k(top_k)

        self._validate_doc_ids(payload.docIds, s.knowledge_base_id)
        idx_override = f"sm_sess_{session_id}"
        dims = [str(x).strip() for x in (payload.dimensions or []) if str(x).strip()]
        if not dims:
            dims = ["Methodology", "Results", "Limitations"]
        question, extra, style = self.chat_service.build_compare_prompt(dimensions=dims)
        kb = knowledgebase_service.get_kb_by_id(
            db=self.db,
            kb_id=int(s.knowledge_base_id),
            user_id=self.current_user.id,
        )
        provider_name = resolve_provider(getattr(kb, "rag_provider", None))
        rag_config = getattr(kb, "rag_config", None)
        graph_boost_ids, graph_boost_chunks, graph_query_variants, graph_debug = (
            self.graph_service.suggest_boost_doc_ids(
            kb_id=int(s.knowledge_base_id),
            query=question,
            provider=provider_name,
            rag_config=rag_config,
            )
        )
        try:
            rq_topk = max(top_k, 8)
            chunks = self.retriever.retrieve(
                query=question,
                kb_id=int(s.knowledge_base_id),
                top_k=rq_topk,
                focus_doc_ids=payload.docIds,
                boost_doc_ids=graph_boost_ids or None,
                boost_chunk_ids=graph_boost_chunks or None,
                session_index=idx_override,
                index_mode="auto",
                provider=provider_name,
                extra_variants=graph_query_variants or None,
            )
            for chunk in chunks:
                metadata = chunk.setdefault("metadata", {})
                metadata["rag_provider"] = provider_name
            provider_stats = build_provider_stats(chunks)
            content = self.chat_service.generate(
                question=question,
                chunks=chunks,
                stream=False,
                history=[],
                compress_history=False,
                style=style,
                extra_system=extra,
            )
        except Exception:
            raise HTTPException(status_code=502, detail="Compare generation failed")

        # 引用契约最终化：右侧引文面板只展示真正在对比表格中被 [N] 引用的来源，
        # 同时把 N 重写为紧凑的 1..K 编号，与 chat_ask 的 UX 完全一致。
        citations = self.rag.build_citations(chunks)
        try:
            content, citations, _finalize_meta = (
                self.chat_service.finalize_answer_with_citations(content or "", citations)
            )
        except Exception:
            pass
        usage = self.chat_service.get_last_usage() or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        debug = {
            "kb_id": s.knowledge_base_id,
            "top_k": top_k,
            "index": idx_override,
            "docIds": payload.docIds,
            "dimensions": dims,
            "retrieval": self.rag.get_last_retrieval_debug() or {},
            "graph": graph_debug,
            "provider_stats": provider_stats,
        }

        try:
            AskEventLogger().log_event(
                {
                    "user_id": str(self.current_user.id),
                    "session_id": session_id,
                    "kb_id": int(s.knowledge_base_id),
                    "question": question[:512],
                    "top_k": int(top_k),
                    "strategy": getattr(settings, "SM_RETRIEVAL_STRATEGY", "multi_stage"),
                    "hits": len(chunks),
                    "retrieval": self.rag.get_last_retrieval_debug() or {},
                    "provider_stats": provider_stats,
                    "graph": graph_debug,
                    "citations": citations,
                    "usage": usage,
                    "answer_chars": len(content or ""),
                    "variant": "compare",
                }
            )
        except Exception:
            pass

        return CompareResponse(
            answer=content or "",
            citations=citations,
            usage=usage,
            debug=debug,
        )

    def _get_session(self, session_id: str):
        s = self.session_service.get_session_by_id(session_id=session_id)
        if not s:
            raise HTTPException(status_code=404, detail="会话不存在")
        if str(self.current_user.id) != str(s.user_id):
            raise HTTPException(status_code=403, detail="无权访问该会话")
        self._ensure_session_kb(s)
        return s

    def _ensure_session_kb(self, session_obj: SessionModel) -> None:
        """Backfill session KB for legacy sessions without bound KB."""
        if session_obj.knowledge_base_id is not None:
            return
        kb_name = f"session_kb_for_{session_obj.session_id}"
        kb = knowledgebase_service.create_kb_for_user(
            db=self.db,
            kb_create=KnowledgeBaseCreate(name=kb_name, description=None, is_ephemeral=True),
            user_id=self.current_user.id,
        )
        session_obj.knowledge_base_id = kb.id
        self.db.add(session_obj)
        self.db.commit()
        self.db.refresh(session_obj)

    def _validate_doc_ids(self, doc_ids: list[int] | None, kb_id: int) -> None:
        try:
            for doc_id in (doc_ids or []):
                document_service.get_document_by_id(
                    db=self.db,
                    doc_id=int(doc_id),
                    user_id=int(self.current_user.id),
                    kb_id=int(kb_id),
                )
        except Exception as exc:
            raise HTTPException(status_code=403, detail=f"无权访问文档或文档不属于该知识库: {exc}") from exc
