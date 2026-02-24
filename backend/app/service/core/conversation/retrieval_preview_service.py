"""Retrieval preview orchestration for debug endpoints."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.config import settings
from models.user import User
from schemas.rag import Chunk as RagChunk
from schemas.retrieval_debug import RetrievalDebugResponse, RetrievalPreviewRequest, PromptSectionDebug
from service import knowledgebase_service
from service.core.components_factory import get_reranker
from service.core.conversation.chat_generation_service import ChatGenerationService
from service.core.conversation.conversation_service import ConversationService
from service.core.rag.retriever import RAGRetriever
from service.core.rag.service import RAGService
from service.core.rag.graph.graph_service import KnowledgeGraphService
from service.core.rag.providers.registry import resolve_provider
from service.core.rag.utils.retrieval_stats import build_provider_stats
from service.session_service import SessionService
from utils.get_logger import logger


class RetrievalPreviewService:
    """Generate retrieval preview debug output."""

    def __init__(self, *, db: Session, current_user: User) -> None:
        """Initialize the preview service.

        Args:
            db (Session): SQLAlchemy session.
            current_user (User): Authenticated user.
        """
        self.db = db
        self.current_user = current_user
        self.rag = RAGService()
        self.chat_service = ChatGenerationService()
        self.retriever = RAGRetriever(self.rag)
        self.conversation_service = ConversationService(db)
        self.graph_service = KnowledgeGraphService(db=db)

    def handle(self, *, payload: RetrievalPreviewRequest) -> RetrievalDebugResponse:
        """Handle retrieval preview.

        Args:
            payload (RetrievalPreviewRequest): Preview request.

        Returns:
            RetrievalDebugResponse: Preview output.
        """
        if not payload.query or not payload.query.strip():
            raise HTTPException(status_code=400, detail="请输入问题或查询内容")

        kb = knowledgebase_service.get_kb_by_id(
            db=self.db,
            kb_id=payload.kb_id,
            user_id=self.current_user.id,
        )
        provider_name = resolve_provider(payload.provider or getattr(kb, "rag_provider", None))
        rag_config = getattr(kb, "rag_config", None)

        session_index = None
        if payload.session_id:
            session_index = self._resolve_session_index(session_id=payload.session_id)

        history_list = None
        history_debug = None
        query_embedding = None
        boost_doc_ids = payload.boost_doc_ids or []
        if payload.session_id:
            try:
                session_service = SessionService(self.db)
                session_obj = session_service.get_session_by_id(session_id=payload.session_id)
                if session_obj:
                    history_list, history_debug, query_embedding = self.conversation_service.build_history_slice(
                        session_id=payload.session_id,
                        question=payload.query,
                    )
                    memory_boost_ids, _memory_debug = self.conversation_service.fetch_focus_doc_ids(
                        user_id=self.current_user.id,
                        session=session_obj,
                        query=payload.query,
                        query_embedding=query_embedding,
                    )
                    if memory_boost_ids:
                        boost_doc_ids = list(set(boost_doc_ids + memory_boost_ids))
            except Exception as mem_err:
                logger.warning("[RETRIEVAL_PREVIEW] Memory enhancement failed: %s", mem_err)

        graph_boost_ids, graph_boost_chunks, graph_query_variants, graph_debug = (
            self.graph_service.suggest_boost_doc_ids(
            kb_id=kb.id,
            query=payload.query,
            provider=provider_name,
            rag_config=rag_config,
            )
        )
        if graph_boost_ids:
            boost_doc_ids = list(set(boost_doc_ids + graph_boost_ids))

        chunks = self.retriever.retrieve(
            query=payload.query,
            kb_id=kb.id,
            top_k=payload.top_k,
            focus_doc_ids=payload.focus_doc_ids,
            boost_doc_ids=boost_doc_ids,
            boost_chunk_ids=graph_boost_chunks or None,
            session_index=session_index,
            index_mode=payload.index_mode or "auto",
            provider=provider_name,
            extra_variants=graph_query_variants or None,
        )
        for chunk in chunks:
            metadata = chunk.setdefault("metadata", {})
            metadata["rag_provider"] = provider_name

        reranked_chunks = chunks
        rerank_enabled = False
        rerank_candidates_preview = []
        rerank_scores_list: list[float] = []
        fast_mode = self._is_fast_mode(provider_name)
        allow_fast_rerank = bool(getattr(settings, "SM_FAST_MODE_RERANK_ENABLED", False))
        enable_rerank = (not fast_mode) or allow_fast_rerank
        try:
            reranker = get_reranker() if enable_rerank else None
            if reranker and chunks:
                rerank_candidates = chunks
                chunk_models = [
                    RagChunk(
                        chunk_id=item.get("chunk_id", ""),
                        document_id=str((item.get("metadata") or {}).get("document_id", "")),
                        content=item.get("text", ""),
                        metadata=item.get("metadata", {}),
                    )
                    for item in rerank_candidates
                ]
                logger.info(
                    "[RETRIEVAL_PREVIEW_RERANK_START] query='%s...' chunks_count=%s",
                    payload.query[:60],
                    len(chunk_models),
                )
                reranked_models = reranker.rerank_sync(payload.query, chunk_models)
                chunk_map = {item.get("chunk_id"): item for item in chunks}
                ordered = [chunk_map.get(model.chunk_id) for model in reranked_models if model.chunk_id in chunk_map]
                remaining = [
                    item
                    for item in chunks
                    if item.get("chunk_id") not in {model.chunk_id for model in reranked_models}
                ]
                reranked_chunks = ([item for item in ordered if item is not None] + remaining)[:payload.top_k]
                rerank_enabled = True
                rerank_candidates_preview = rerank_candidates
                for model in reranked_models:
                    if hasattr(model, "metadata") and model.metadata:
                        score = model.metadata.get("rerank_score")
                        if score is not None:
                            rerank_scores_list.append(float(score))
                            for chunk in reranked_chunks:
                                if chunk.get("chunk_id") == model.chunk_id:
                                    if chunk.get("metadata") is None:
                                        chunk["metadata"] = {}
                                    chunk["metadata"]["rerank_score"] = float(score)
                                    break
                logger.info(
                    "[RETRIEVAL_PREVIEW_RERANK_COMPLETE] reranked_count=%s original_count=%s final_chunks=%s scores=%s",
                    len(reranked_models),
                    len(chunk_models),
                    len(reranked_chunks),
                    rerank_scores_list[:3] if rerank_scores_list else "N/A",
                )
            else:
                logger.info(
                    "[RETRIEVAL_PREVIEW_RERANK_SKIP] reranker=%s chunks=%s enable_rerank=%s",
                    reranker is not None,
                    len(chunks) if chunks else 0,
                    enable_rerank,
                )
        except Exception as rerank_exc:
            logger.warning(
                "[RETRIEVAL_PREVIEW_RERANK_FAILED] Cross-encoder rerank failed: %s",
                rerank_exc,
                exc_info=True,
            )

        debug = self.retriever.get_last_retrieval_debug() or {}
        prompt_sections_raw = self.chat_service.build_prompt_sections(
            question=payload.query,
            chunks=reranked_chunks,
            history_summary=None,
            style=None,
            extra_system=None,
        )
        prompt_sections: list[PromptSectionDebug] = [
            PromptSectionDebug(role=section.role, content=section.content, length=len(section.content))
            for section in prompt_sections_raw
        ]
        prompt_total_chars = sum(section.length for section in prompt_sections)
        context_chars = 0
        for section in prompt_sections:
            if "[Context]" in section.content:
                context_chars = section.length
                break

        provider_stats = build_provider_stats(reranked_chunks)
        return RetrievalDebugResponse(
            kb_id=kb.id,
            query=payload.query,
            top_k=payload.top_k,
            rag_provider=provider_name,
            provider_stats=provider_stats,
            graph=graph_debug,
            variant_meta=debug.get("query_meta") or {},
            variants=debug.get("variants") or [],
            index_plan=debug.get("index_plan") or [],
            index_mode=debug.get("index_mode"),
            indices_used=debug.get("indices_used") or [],
            index_stats=debug.get("index_stats") or {},
            path_stats=debug.get("path_stats") or {},
            path_samples=self._coerce_path_samples(debug.get("path_samples")),
            rrf_candidates=self._coerce_chunk_previews(
                debug.get("rrf_details") or debug.get("rrf_candidates")
            ),
            rrf_candidates_count=debug.get("rrf_candidates_count"),
            mmr_chunks=self._coerce_chunk_previews(debug.get("mmr_chunks")),
            mmr_output_count=debug.get("mmr_output_count"),
            rerank_top_k=debug.get("rerank_top_k"),
            rerank_candidates=self._coerce_chunk_previews(rerank_candidates_preview),
            rerank_scores=rerank_scores_list,
            rerank_enabled=rerank_enabled,
            final_chunks=self._coerce_chunk_previews(reranked_chunks),
            memory=debug.get("memory") or {},
            prompt_sections=prompt_sections,
            prompt_total_chars=prompt_total_chars,
            prompt_context_chars=context_chars,
        )

    @staticmethod
    def _is_fast_mode(provider: Optional[str]) -> bool:
        provider_norm = (provider or "").strip().lower()
        return provider_norm not in {"graph", "multimodal_graph"}

    def _resolve_session_index(self, *, session_id: str) -> str:
        session_service = SessionService(self.db)
        session_obj = session_service.get_session_by_id(session_id=session_id)
        if not session_obj:
            raise HTTPException(status_code=404, detail="指定的会话不存在")
        if str(session_obj.user_id) != str(self.current_user.id):
            raise HTTPException(status_code=403, detail="无权限访问该会话")
        return f"sm_sess_{session_id}"

    @staticmethod
    def _coerce_chunk_previews(items: list | None):
        normalized: list = []
        for item in items or []:
            if isinstance(item, dict):
                meta = item.get("metadata") or {}
                score = meta.get("rerank_score") or item.get("score")
                normalized.append(
                    {
                        "chunk_id": item.get("chunk_id"),
                        "score": score,
                        "document_id": meta.get("document_id"),
                        "page": meta.get("page"),
                        "source": meta.get("retrieval_source"),
                        "element_type": meta.get("element_type"),
                        "logical_type": meta.get("logical_type"),
                        "text_preview": item.get("text_preview") or item.get("text"),
                        "metadata": meta,
                    }
                )
            else:
                normalized.append({"chunk_id": str(item), "metadata": {}})
        return normalized

    @staticmethod
    def _coerce_path_samples(samples: list | None):
        normalized: list = []
        for sample in samples or []:
            if isinstance(sample, dict):
                normalized.append(
                    {
                        "path_id": sample.get("path_id") or "",
                        "label": sample.get("label") or "",
                        "query_tag": sample.get("query_tag") or "",
                        "source": sample.get("source"),
                        "hit_count": int(sample.get("hit_count") or 0),
                        "hits": RetrievalPreviewService._coerce_chunk_previews(sample.get("hits")),
                    }
                )
        return normalized
