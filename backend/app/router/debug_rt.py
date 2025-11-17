from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from models.user import User
from schemas.retrieval_debug import RetrievalPreviewRequest, RetrievalDebugResponse, PromptSectionDebug
from service import knowledgebase_service
from service.auth import get_current_user
from service.core.rag.service import RAGService
from service.core.components_factory import get_reranker
from service.core.rag.history import ShortTermMemoryBuilder
from service.memory_service import LongTermMemoryService
from schemas.rag import Chunk as RagChunk
from service.session_service import SessionService
from utils.database import get_db
from utils.get_logger import logger
from dataclasses import asdict
import asyncio
from core.config import settings


router = APIRouter()


def _resolve_session_index(db: Session, session_id: str, current_user: User) -> str:
    session_service = SessionService(db)
    session_obj = session_service.get_session_by_id(session_id=session_id)
    if not session_obj:
        raise HTTPException(status_code=404, detail="指定的会话不存在")
    if str(session_obj.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="无权限访问该会话")
    return f"sm_sess_{session_id}"


@router.post(
    "/retrieval-preview",
    response_model=RetrievalDebugResponse,
    summary="检索/精排调试预览",
    description="执行多阶段检索并返回各阶段调试信息、候选列表及最终的 LLM 输入片段。",
)
def retrieval_preview(
    payload: RetrievalPreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RetrievalDebugResponse:
    if not payload.query or not payload.query.strip():
        raise HTTPException(status_code=400, detail="请输入问题或查询内容")

    kb = knowledgebase_service.get_kb_by_id(db=db, kb_id=payload.kb_id, user_id=current_user.id)

    session_index = None
    if payload.session_id:
        session_index = _resolve_session_index(db=db, session_id=payload.session_id, current_user=current_user)

    rag = RAGService()
    stm_builder = ShortTermMemoryBuilder(db)
    memory_service = LongTermMemoryService(db)
    
    # 构建历史记录（与 /ask 接口保持一致，用于记忆增强）
    history_list, history_debug, query_embedding = None, None, None
    boost_doc_ids = payload.boost_doc_ids or []
    if payload.session_id:
        try:
            session_service = SessionService(db)
            session_obj = session_service.get_session_by_id(session_id=payload.session_id)
            if session_obj:
                history_list, history_debug, query_embedding = stm_builder.build_history(
                    session_id=payload.session_id,
                    question=payload.query,
                )
                # 获取记忆增强的doc_ids（与 /ask 接口保持一致）
                memory_boost_ids, memory_debug_raw = memory_service.fetch_focus_doc_ids(
                    user_id=current_user.id,
                    session=session_obj,
                    query=payload.query,
                    query_embedding=query_embedding,
                )
                if memory_boost_ids:
                    boost_doc_ids = list(set(boost_doc_ids + memory_boost_ids))
        except Exception as mem_err:
            logger.warning(f"[RETRIEVAL_PREVIEW] Memory enhancement failed: {mem_err}")
    
    # 执行检索（与 /ask 接口保持一致）
    # 注意：debug_rt.py 只处理单个知识库，rag.retrieve 已经去重，不需要再次去重
    chunks = rag.retrieve(
        query=payload.query,
        kb_id=kb.id,
        top_k=payload.top_k,
        focus_doc_ids=payload.focus_doc_ids,
        boost_doc_ids=boost_doc_ids,  # 使用记忆增强的doc_ids
        session_index=session_index,
        index_mode=payload.index_mode or "auto",
    )
    # 注意：rag.retrieve 返回的是 MMR 输出的所有候选（SM_L2_RERANK_TOPK 个），已经是去重后的
    # 单个知识库场景下，不需要再次去重，直接使用所有候选供精排

    # L2 Cross-Encoder rerank (与 /ask 接口保持一致)
    # 注意：rag.retrieve 已经返回了 MMR 输出的候选（SM_L2_RERANK_TOPK 个），这里直接精排所有候选
    reranked_chunks = chunks
    rerank_enabled = False
    rerank_candidates_preview = []
    rerank_scores_list = []
    try:
        reranker = get_reranker()
        if reranker and chunks:
            # 精排所有 MMR 输出的候选（rag.retrieve 已经返回了 MMR 输出的候选）
            rerank_candidates = chunks  # 所有候选都参与精排
            chunk_models = [
                RagChunk(
                    chunk_id=item.get("chunk_id", ""),
                    document_id=str((item.get("metadata") or {}).get("document_id", "")),
                    content=item.get("text", ""),
                    metadata=item.get("metadata", {}),
                )
                for item in rerank_candidates
            ]
            logger.info(f"[RETRIEVAL_PREVIEW_RERANK_START] query='{payload.query[:60]}...' chunks_count={len(chunk_models)}")
            reranked_models = asyncio.run(reranker.rerank(payload.query, chunk_models))
            chunk_map = {item.get("chunk_id"): item for item in chunks}
            ordered = [chunk_map.get(model.chunk_id) for model in reranked_models if model.chunk_id in chunk_map]
            remaining = [item for item in chunks if item.get("chunk_id") not in {model.chunk_id for model in reranked_models}]
            # 精排后取 top_k
            reranked_chunks = ([item for item in ordered if item is not None] + remaining)[:payload.top_k]
            
            # 保存精排信息用于前端展示
            rerank_enabled = True
            rerank_candidates_preview = rerank_candidates  # 精排前的候选
            
            # 从reranked_models的metadata中提取精排分数，并更新到reranked_chunks中
            rerank_scores_list = []
            for model in reranked_models:
                if hasattr(model, 'metadata') and model.metadata:
                    score = model.metadata.get('rerank_score')
                    if score is not None:
                        rerank_scores_list.append(float(score))
                        # 同时更新reranked_chunks中对应chunk的metadata
                        for chunk in reranked_chunks:
                            if chunk.get("chunk_id") == model.chunk_id:
                                if chunk.get("metadata") is None:
                                    chunk["metadata"] = {}
                                chunk["metadata"]["rerank_score"] = float(score)
                                break
            
            logger.info(f"[RETRIEVAL_PREVIEW_RERANK_COMPLETE] reranked_count={len(reranked_models)} original_count={len(chunk_models)} final_chunks={len(reranked_chunks)} scores={rerank_scores_list[:3] if rerank_scores_list else 'N/A'}")
        else:
            logger.info(f"[RETRIEVAL_PREVIEW_RERANK_SKIP] reranker={reranker is not None} chunks={len(chunks) if chunks else 0}")
    except Exception as rerank_exc:
        # 精排失败不影响检索预览，继续使用原始chunks
        logger.warning(f"[RETRIEVAL_PREVIEW_RERANK_FAILED] Cross-encoder rerank failed: {rerank_exc}", exc_info=True)

    debug = rag.get_last_retrieval_debug() or {}

    prompt_sections_raw = rag.prompt.build(
        question=payload.query,
        chunks=reranked_chunks,  # 使用精排后的chunks
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

    def _coerce_chunk_previews(items: list | None):
        normalized = []
        for item in items or []:
            if isinstance(item, dict):
                meta = item.get("metadata") or {}
                # 优先使用rerank_score（精排分数），其次使用原始score
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
                normalized.append(
                    {
                        "chunk_id": str(item),
                        "metadata": {},
                    }
                )
        return normalized

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
                        "hits": _coerce_chunk_previews(sample.get("hits")),
                    }
                )
        return normalized

    return RetrievalDebugResponse(
        kb_id=kb.id,
        query=payload.query,
        top_k=payload.top_k,
        variant_meta=debug.get("query_meta") or {},
        variants=debug.get("variants") or [],
        index_plan=debug.get("index_plan") or [],
        index_mode=debug.get("index_mode"),
        indices_used=debug.get("indices_used") or [],
        index_stats=debug.get("index_stats") or {},
        path_stats=debug.get("path_stats") or {},
        path_samples=_coerce_path_samples(debug.get("path_samples")),
        rrf_candidates=_coerce_chunk_previews(debug.get("rrf_details") or debug.get("rrf_candidates")),  # RRF预览样本（用于展示）
        rrf_candidates_count=debug.get("rrf_candidates_count"),  # RRF融合后的实际候选数
        mmr_chunks=_coerce_chunk_previews(debug.get("mmr_chunks")),
        mmr_output_count=debug.get("mmr_output_count"),  # MMR输出的候选数
        rerank_top_k=debug.get("rerank_top_k"),  # 精排候选数
        rerank_candidates=_coerce_chunk_previews(rerank_candidates_preview),  # 精排前的候选
        rerank_scores=rerank_scores_list,  # 精排分数列表
        rerank_enabled=rerank_enabled,  # 是否启用了精排
        # final_chunks 应该使用精排后的结果（reranked_chunks），而不是 rag.retrieve() 返回的候选
        # 因为 rag.retrieve() 返回的是 MMR 输出的候选（给精排的），不是最终结果
        final_chunks=_coerce_chunk_previews(reranked_chunks),  # 使用精排后的chunks作为最终结果
        memory=debug.get("memory") or {},
        prompt_sections=prompt_sections,
        prompt_total_chars=prompt_total_chars,
        prompt_context_chars=context_chars,
    )

