"""Retrieval provider compare service."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models.user import User
from schemas.retrieval_debug import RetrievalCompareRequest, RetrievalCompareResponse, RetrievalCompareSide
from service import knowledgebase_service
from service.core.rag.graph.graph_service import KnowledgeGraphService
from service.core.rag.providers.registry import resolve_provider
from service.core.rag.retriever import RAGRetriever
from service.core.rag.service import RAGService
from service.core.rag.utils.retrieval_stats import build_provider_stats
from service.session_service import SessionService


class RetrievalCompareService:
    """Compare retrieval results between two providers."""

    def __init__(self, *, db: Session, current_user: User) -> None:
        self.db = db
        self.current_user = current_user
        self.rag = RAGService()
        self.retriever = RAGRetriever(self.rag)
        self.graph_service = KnowledgeGraphService(db=db)
        self.session_service = SessionService(db)

    def handle(self, *, payload: RetrievalCompareRequest) -> RetrievalCompareResponse:
        kb = knowledgebase_service.get_kb_by_id(
            db=self.db,
            kb_id=payload.kb_id,
            user_id=self.current_user.id,
        )
        session_index = None
        if payload.session_id:
            session_index = self._resolve_session_index(payload.session_id)

        side_a, chunks_a = self._run_provider(
            provider_raw=payload.provider_a,
            kb_id=payload.kb_id,
            query=payload.query,
            top_k=payload.top_k,
            focus_doc_ids=payload.focus_doc_ids,
            index_mode=payload.index_mode or "auto",
            session_index=session_index,
            kb=kb,
        )
        side_b, chunks_b = self._run_provider(
            provider_raw=payload.provider_b,
            kb_id=payload.kb_id,
            query=payload.query,
            top_k=payload.top_k,
            focus_doc_ids=payload.focus_doc_ids,
            index_mode=payload.index_mode or "auto",
            session_index=session_index,
            kb=kb,
        )

        overlap = self._build_overlap(chunks_a, chunks_b)
        panel = self._build_panel(side_a, side_b, chunks_a, chunks_b, overlap)
        return RetrievalCompareResponse(
            kb_id=payload.kb_id,
            query=payload.query,
            top_k=payload.top_k,
            provider_a=side_a.provider,
            provider_b=side_b.provider,
            overlap=overlap,
            panel=panel,
            a=side_a,
            b=side_b,
        )

    def _run_provider(
        self,
        *,
        provider_raw: str,
        kb_id: int,
        query: str,
        top_k: int,
        focus_doc_ids: Optional[List[int]],
        index_mode: str,
        session_index: Optional[str],
        kb,
    ) -> Tuple[RetrievalCompareSide, List[Dict[str, Any]]]:
        provider_name = resolve_provider(provider_raw or getattr(kb, "rag_provider", None))
        rag_config = getattr(kb, "rag_config", None)
        graph_boost_ids, graph_boost_chunks, graph_query_variants, graph_debug = (
            self.graph_service.suggest_boost_doc_ids(
            kb_id=kb_id,
            query=query,
            provider=provider_name,
            rag_config=rag_config,
            )
        )

        started = time.time()
        chunks = self.retriever.retrieve(
            query=query,
            kb_id=kb_id,
            top_k=top_k,
            focus_doc_ids=focus_doc_ids,
            boost_doc_ids=graph_boost_ids or None,
            boost_chunk_ids=graph_boost_chunks or None,
            session_index=session_index,
            index_mode=index_mode,
            provider=provider_name,
            extra_variants=graph_query_variants or None,
        )
        latency_ms = int((time.time() - started) * 1000)
        for chunk in chunks:
            metadata = chunk.setdefault("metadata", {})
            metadata["rag_provider"] = provider_name

        provider_stats = build_provider_stats(chunks)
        retrieval_debug = self.retriever.get_last_retrieval_debug() or {}
        side = RetrievalCompareSide(
            provider=provider_name,
            latency_ms=latency_ms,
            index_mode=retrieval_debug.get("index_mode") or index_mode,
            indices_used=retrieval_debug.get("indices_used") or [],
            chunks=self._coerce_chunk_previews(chunks),
            provider_stats=provider_stats,
            graph=graph_debug,
            retrieval=retrieval_debug,
        )
        return side, chunks

    def _resolve_session_index(self, session_id: str) -> str:
        session_obj = self.session_service.get_session_by_id(session_id=session_id)
        if not session_obj:
            raise HTTPException(status_code=404, detail="会话不存在")
        if str(session_obj.user_id) != str(self.current_user.id):
            raise HTTPException(status_code=403, detail="无权访问该会话")
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
    def _build_overlap(chunks_a: List[Dict[str, Any]], chunks_b: List[Dict[str, Any]]) -> Dict[str, Any]:
        def _extract_ids(chunks: List[Dict[str, Any]]) -> Tuple[set[str], set[str]]:
            chunk_ids: set[str] = set()
            doc_ids: set[str] = set()
            for item in chunks or []:
                chunk_id = str(item.get("chunk_id") or "").strip()
                if chunk_id:
                    chunk_ids.add(chunk_id)
                metadata = item.get("metadata") or {}
                doc_id = str(metadata.get("document_id") or "").strip()
                if doc_id:
                    doc_ids.add(doc_id)
            return chunk_ids, doc_ids

        chunk_ids_a, doc_ids_a = _extract_ids(chunks_a)
        chunk_ids_b, doc_ids_b = _extract_ids(chunks_b)

        chunk_overlap = chunk_ids_a & chunk_ids_b
        chunk_union = chunk_ids_a | chunk_ids_b
        doc_overlap = doc_ids_a & doc_ids_b
        doc_union = doc_ids_a | doc_ids_b

        return {
            "chunk_overlap_count": len(chunk_overlap),
            "chunk_union_count": len(chunk_union),
            "chunk_overlap_ratio": len(chunk_overlap) / max(len(chunk_union), 1),
            "chunk_unique_a": len(chunk_ids_a - chunk_ids_b),
            "chunk_unique_b": len(chunk_ids_b - chunk_ids_a),
            "doc_overlap_count": len(doc_overlap),
            "doc_union_count": len(doc_union),
            "doc_overlap_ratio": len(doc_overlap) / max(len(doc_union), 1),
        }

    @staticmethod
    def _build_panel(
        side_a: RetrievalCompareSide,
        side_b: RetrievalCompareSide,
        chunks_a: List[Dict[str, Any]],
        chunks_b: List[Dict[str, Any]],
        overlap: Dict[str, Any],
    ) -> Dict[str, Any]:
        def _extract_doc_ids(chunks: List[Dict[str, Any]]) -> List[str]:
            doc_ids: List[str] = []
            seen: set[str] = set()
            for item in chunks or []:
                meta = item.get("metadata") or {}
                doc_id = str(meta.get("document_id") or "").strip()
                if not doc_id or doc_id in seen:
                    continue
                seen.add(doc_id)
                doc_ids.append(doc_id)
            return doc_ids

        doc_ids_a = _extract_doc_ids(chunks_a)
        doc_ids_b = _extract_doc_ids(chunks_b)
        set_a = set(doc_ids_a)
        set_b = set(doc_ids_b)
        both = list(set_a & set_b)
        only_a = list(set_a - set_b)
        only_b = list(set_b - set_a)

        return {
            "overlap": overlap,
            "provider_a": {
                "provider": side_a.provider,
                "latency_ms": side_a.latency_ms,
                "chunk_count": side_a.provider_stats.get(side_a.provider, {}).get("chunk_count"),
                "doc_count": side_a.provider_stats.get(side_a.provider, {}).get("doc_count"),
                "element_type": side_a.provider_stats.get(side_a.provider, {}).get("element_type"),
                "source": side_a.provider_stats.get(side_a.provider, {}).get("source"),
            },
            "provider_b": {
                "provider": side_b.provider,
                "latency_ms": side_b.latency_ms,
                "chunk_count": side_b.provider_stats.get(side_b.provider, {}).get("chunk_count"),
                "doc_count": side_b.provider_stats.get(side_b.provider, {}).get("doc_count"),
                "element_type": side_b.provider_stats.get(side_b.provider, {}).get("element_type"),
                "source": side_b.provider_stats.get(side_b.provider, {}).get("source"),
            },
            "doc_overlap": {
                "both": both[:10],
                "only_a": only_a[:10],
                "only_b": only_b[:10],
            },
        }
