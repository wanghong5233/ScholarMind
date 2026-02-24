"""Session retrieval service."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.config import settings
from models.session import Session as SessionModel
from models.user import User
from schemas.knowledge_base import KnowledgeBaseCreate
from schemas.rag import Chunk as RagChunk
from service.core.rag.retriever import RAGRetriever
from service.core.rag.graph.graph_service import KnowledgeGraphService
from service.core.rag.providers.registry import resolve_provider
from service import knowledgebase_service
from service.session_service import SessionService


class SessionRetrievalService:
    """Handle session retrieval requests."""

    def __init__(self, *, db: Session, current_user: User) -> None:
        """Initialize the retrieval service."""
        self.db = db
        self.current_user = current_user
        self.session_service = SessionService(db)
        self.retriever = RAGRetriever()

    def retrieve(
        self,
        *,
        session_id: str,
        q: str,
        top_k: int,
        focus_doc_ids: Optional[str],
        use_session_index: bool,
        index_mode: Optional[str],
        provider: Optional[str] = None,
    ) -> list[RagChunk]:
        """Retrieve chunks for a session."""
        s = self._get_session(session_id)
        if not s.knowledge_base_id:
            raise HTTPException(status_code=400, detail="该会话未绑定知识库")

        if s.defaults_json:
            try:
                data = json.loads(s.defaults_json)
                if isinstance(data, dict) and isinstance(data.get("topK"), int):
                    top_k = data.get("topK") or top_k
            except Exception:
                pass

        session_index = f"sm_sess_{session_id}"
        effective_mode = (
            index_mode if isinstance(index_mode, str) else ("session_only" if use_session_index else "global_only")
        )
        focus_ids_list = None
        if focus_doc_ids:
            try:
                focus_ids_list = [int(x) for x in focus_doc_ids.split(",") if x.strip().isdigit()]
            except Exception:
                focus_ids_list = None

        kb = knowledgebase_service.get_kb_by_id(
            db=self.db,
            kb_id=int(s.knowledge_base_id),
            user_id=self.current_user.id,
        )
        provider_name = resolve_provider(provider or getattr(kb, "rag_provider", None))
        rag_config = getattr(kb, "rag_config", None)
        graph_service = KnowledgeGraphService(db=self.db)
        graph_boost_ids, graph_boost_chunks, graph_query_variants, _graph_debug = (
            graph_service.suggest_boost_doc_ids(
            kb_id=int(s.knowledge_base_id),
            query=q,
            provider=provider_name,
            rag_config=rag_config,
            )
        )

        results = self.retriever.retrieve(
            query=q,
            kb_id=int(s.knowledge_base_id),
            top_k=top_k,
            focus_doc_ids=focus_ids_list,
            boost_doc_ids=graph_boost_ids or None,
            boost_chunk_ids=graph_boost_chunks or None,
            session_index=session_index,
            index_mode=effective_mode,
            provider=provider_name,
            extra_variants=graph_query_variants or None,
        )

        out: list[RagChunk] = []
        for item in results:
            md = item.get("metadata") or {}
            md["rag_provider"] = provider_name
            out.append(
                RagChunk(
                    chunk_id=str(item.get("chunk_id", "")),
                    document_id=str(md.get("document_id", "")),
                    content=item.get("text", ""),
                    metadata=md,
                )
            )
        return out

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
