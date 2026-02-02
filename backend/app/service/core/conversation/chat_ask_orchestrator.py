"""Chat ask orchestration service."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from core.config import settings
from models.message import Message
from models.user import User
from schemas.rag import Chunk as RagChunk
from schemas.session import SessionDefaults
from service.core.components_factory import get_reranker
from service.core.conversation.ask_utils import normalize_top_k
from service.core.conversation.chat_generation_service import ChatGenerationService
from service.core.conversation.conversation_service import ConversationService
from service.core.rag.retriever import RAGRetriever
from service.core.rag.service import RAGService
from service.core.rag.graph.graph_service import KnowledgeGraphService
from service.core.rag.providers.registry import resolve_provider
from service.core.rag.utils.retrieval_stats import build_provider_stats
from service import knowledgebase_service
from service.memory_service import LongTermMemoryService
from service.session_service import SessionService
from utils.ask_logger import AskEventLogger
from utils.experiments import assign_variant
from utils.get_logger import logger
from utils.quota import quota
from utils.rate_limiter import rate_limiter


class ChatAskOrchestrator:
    """Orchestrate chat ask flow for sessions."""

    def __init__(self, *, db: Session, current_user: User) -> None:
        """Initialize the ask orchestrator.

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
        self.memory_service = LongTermMemoryService(db)
        self.conversation_service = ConversationService(db, ltm_service=self.memory_service)
        self.graph_service = KnowledgeGraphService(db=db)

    def handle(self, *, session_id: str, payload: Dict[str, Any]) -> JSONResponse | StreamingResponse:
        """Handle a session ask request.

        Args:
            session_id (str): Session identifier.
            payload (Dict[str, Any]): Request payload.

        Returns:
            JSONResponse | StreamingResponse: Response to return from the route.
        """
        s = self._get_session(session_id)
        question = (payload or {}).get("question") or ""
        stream = bool((payload or {}).get("stream", True))
        compress_history = bool((payload or {}).get("compressHistory", False))
        focus_ids = payload.get("focusDocIds") if isinstance(payload.get("focusDocIds"), list) else None
        raw_index_mode = payload.get("indexMode") if isinstance(payload.get("indexMode"), str) else None
        index_mode = raw_index_mode or "auto"
        replace_from_message = self._resolve_replace_message(session_id, payload)

        bucket = f"ask:{self.current_user.id}:{session_id}"
        if not rate_limiter.check_and_consume(bucket, limit=60, window_seconds=60):
            raise HTTPException(status_code=429, detail="Too Many Requests")

        qkey = f"ask:day:{self.current_user.id}:{int(__import__('time').time())//86400}"
        if not quota.consume_count(qkey, settings.DAILY_ASK_COUNT, window_seconds=86400):
            raise HTTPException(status_code=429, detail="Daily ask quota exceeded")

        try:
            logger.info(
                "ASK user=%s session=%s q='%s' topK=%s",
                self.current_user.id,
                session_id,
                str(question)[:80],
                payload.get("topK"),
            )
        except Exception:
            pass

        top_k = payload.get("topK") if isinstance(payload.get("topK"), int) else None
        temperature = payload.get("temperature") if isinstance(payload.get("temperature"), (int, float)) else None
        max_tokens = payload.get("maxTokens") if isinstance(payload.get("maxTokens"), int) else None

        defaults_raw: dict[str, Any] = {}
        defaults_model = SessionDefaults()
        if s.defaults_json:
            try:
                defaults_raw = json.loads(s.defaults_json)
                defaults_model = SessionDefaults(**defaults_raw)
            except Exception:
                defaults_raw = {}
                defaults_model = SessionDefaults()

        if defaults_raw:
            if top_k is None:
                top_k = None
            if temperature is None and isinstance(defaults_raw.get("temperature"), (int, float)):
                temperature = defaults_raw.get("temperature")
            if max_tokens is None and isinstance(defaults_raw.get("maxTokens"), int):
                max_tokens = defaults_raw.get("maxTokens")

        top_k = normalize_top_k(top_k)
        temperature = temperature if isinstance(temperature, (int, float)) else settings.SM_TEMPERATURE
        max_tokens = max_tokens if isinstance(max_tokens, int) else settings.SM_MAX_TOKENS
        provider_override = payload.get("ragProvider") or payload.get("provider")
        if not isinstance(provider_override, str):
            provider_override = defaults_model.retrievalStrategy

        session_kb_id: Optional[int] = int(s.knowledge_base_id) if s.knowledge_base_id is not None else None
        use_session_kb = bool(defaults_model.useSessionKnowledgeBase and session_kb_id is not None)
        user_kb_id: Optional[int] = (
            int(defaults_model.userKnowledgeBaseId)
            if defaults_model.userKnowledgeBaseId is not None
            else None
        )
        use_user_kb = bool(defaults_model.useUserKnowledgeBase and user_kb_id is not None)

        retrieval_plan: list[tuple[str, int]] = []
        if use_session_kb and session_kb_id is not None:
            retrieval_plan.append(("session", session_kb_id))
        if use_user_kb and user_kb_id is not None and user_kb_id != session_kb_id:
            retrieval_plan.append(("user", user_kb_id))

        if not retrieval_plan and use_user_kb and user_kb_id is not None and user_kb_id == session_kb_id:
            retrieval_plan.append(("user", user_kb_id))

        if not retrieval_plan:
            effective_index_mode = "disabled"
        elif len(retrieval_plan) == 1:
            effective_index_mode = "session_only" if retrieval_plan[0][0] == "session" else "global_only"
        else:
            effective_index_mode = "hybrid"

        index_mode = effective_index_mode
        primary_kb_for_debug: Optional[int] = session_kb_id if session_kb_id is not None else user_kb_id
        kb_ids_for_debug = [kb for _, kb in retrieval_plan]
        if not kb_ids_for_debug:
            primary_kb_for_debug = None

        variant = assign_variant(
            user_id=self.current_user.id,
            session_id=session_id,
            key="ask_mq_rrf",
            buckets=("A", "B"),
        )
        session_index_name = f"sm_sess_{session_id}"

        def merge_chunks(candidates: list, limit: int):
            if not candidates or not isinstance(limit, int) or limit <= 0:
                return []

            def _score(chunk):
                metadata = chunk.get("metadata") or {}
                return float(
                    metadata.get("retrieval_score")
                    or metadata.get("fused_score")
                    or chunk.get("score")
                    or 0.0
                )

            ordered = sorted(candidates, key=_score, reverse=True)
            merged = []
            seen = set()
            for chunk in ordered:
                metadata = chunk.get("metadata") or {}
                key = (
                    chunk.get("chunk_id"),
                    metadata.get("document_id"),
                    metadata.get("knowledge_base_id"),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(chunk)
                if len(merged) >= limit:
                    break
            return merged

        def perform_retrieval(
            *,
            question: str,
            top_k: int,
            focus_ids: Optional[list[int]],
            boost_ids: Optional[list[int]],
        ):
            if not retrieval_plan:
                return [], {}, {}, "disabled"
            all_candidates: list = []
            sources_debug: dict[str, Any] = {}
            latest_debug: dict[str, Any] = {}
            for label, kb_id_value in retrieval_plan:
                mode_override = "session_only" if label == "session" else "global_only"
                provider_name, rag_config = self._resolve_kb_provider(
                    kb_id_value, provider_override=provider_override
                )
                graph_boost_ids, graph_boost_chunks, graph_query_variants, graph_debug = (
                    self.graph_service.suggest_boost_doc_ids(
                    kb_id=kb_id_value,
                    query=question,
                    provider=provider_name,
                    rag_config=rag_config,
                    )
                )
                merged_boost_ids = list({*(boost_ids or []), *graph_boost_ids})
                subset = self.retriever.retrieve(
                    query=question,
                    kb_id=kb_id_value,
                    top_k=top_k,
                    focus_doc_ids=focus_ids,
                    boost_doc_ids=merged_boost_ids or None,
                    boost_chunk_ids=graph_boost_chunks or None,
                    session_index=session_index_name if label == "session" else None,
                    index_mode=mode_override,
                    provider=provider_name,
                    extra_variants=graph_query_variants or None,
                )
                for chunk in subset:
                    metadata = chunk.setdefault("metadata", {})
                    metadata["knowledge_base_scope"] = label
                    metadata["knowledge_base_id"] = kb_id_value
                    metadata["rag_provider"] = provider_name
                all_candidates.extend(subset)
                debug_snapshot = self.retriever.get_last_retrieval_debug() or {}
                if graph_debug:
                    debug_snapshot["graph"] = graph_debug
                sources_debug[label] = debug_snapshot
                latest_debug = debug_snapshot
            if len(retrieval_plan) == 1:
                merged = all_candidates
            else:
                rerank_top_k = max(
                    top_k,
                    int(getattr(settings, "SM_L2_RERANK_TOPK", 20) or 20),
                )
                merged = merge_chunks(all_candidates, rerank_top_k * 2)
            index_descriptor = " | ".join(f"{label}:{kb_id}" for label, kb_id in retrieval_plan) or "disabled"
            return merged, latest_debug, sources_debug, index_descriptor

        context_files = []
        context_text_for_llm = ""
        if s.context_json:
            try:
                context_data = json.loads(s.context_json) if isinstance(s.context_json, str) else s.context_json
                uploaded_files = context_data.get("uploaded_files", [])
                if uploaded_files:
                    context_files = [
                        {"filename": f.get("filename"), "uploaded_at": f.get("uploaded_at")}
                        for f in uploaded_files
                    ]
                    file_texts = []
                    for f in uploaded_files:
                        file_texts.append(
                            f"--- 文件: {f.get('filename')} ---\n{f.get('content', '')}\n--- 文件结束 ---"
                        )
                    context_text_for_llm = "\n\n".join(file_texts)
                    logger.info(
                        "[CONTEXT_JSON_LOADED] session=%s files_count=%s context_text_len=%s",
                        session_id,
                        len(uploaded_files),
                        len(context_text_for_llm),
                    )
            except Exception as ctx_err:
                logger.warning("Failed to parse context_json: %s", ctx_err)

        if stream:
            def gen():
                try:
                    history_list, history_debug, query_embedding = self.conversation_service.build_history_slice(
                        session_id=session_id,
                        question=question,
                    )
                    history_debug_dict = asdict(history_debug)
                    boost_doc_ids, memory_debug_raw = self.conversation_service.fetch_focus_doc_ids(
                        user_id=self.current_user.id,
                        session=s,
                        query=question,
                        query_embedding=query_embedding,
                    )
                    chunks0, retrieval_debug, retrieval_sources, idx_override = perform_retrieval(
                        question=question,
                        top_k=top_k,
                        focus_ids=focus_ids,
                        boost_ids=boost_doc_ids,
                    )
                    try:
                        reranker_stream = get_reranker()
                    except Exception:
                        reranker_stream = None
                    if reranker_stream and chunks0:
                        try:
                            stream_models = [
                                RagChunk(
                                    chunk_id=item.get("chunk_id", ""),
                                    document_id=str((item.get("metadata") or {}).get("document_id", "")),
                                    content=item.get("text", ""),
                                    metadata=item.get("metadata", {}),
                                )
                                for item in chunks0
                            ]
                            logger.info(
                                "[RERANK_START_STREAM] query='%s...' chunks_count=%s top_k=%s",
                                question[:60],
                                len(stream_models),
                                top_k,
                            )
                            reranked_stream = asyncio.run(reranker_stream.rerank(question, stream_models))  # type: ignore[arg-type]
                            chunk_stream_map = {item.get("chunk_id"): item for item in chunks0}
                            ordered_stream = [
                                chunk_stream_map.get(model.chunk_id)
                                for model in reranked_stream
                                if model.chunk_id in chunk_stream_map
                            ]
                            remaining_stream = [
                                item
                                for item in chunks0
                                if item.get("chunk_id") not in {model.chunk_id for model in reranked_stream}
                            ]
                            chunks0 = ([item for item in ordered_stream if item is not None] + remaining_stream)[:top_k]
                            logger.info(
                                "[RERANK_COMPLETE_STREAM] reranked_count=%s final_chunks=%s",
                                len(reranked_stream),
                                len(chunks0),
                            )
                        except Exception as rerank_exc:
                            logger.warning(
                                "[RERANK_FAILED_STREAM] Cross-encoder rerank failed: %s",
                                rerank_exc,
                                exc_info=True,
                            )
                    import json as _json
                    progress_payload = {
                        "stage": "retrieved",
                        "hits": len(chunks0),
                        "index": idx_override,
                        "index_mode": index_mode,
                        "variant": variant,
                        "retrieval": retrieval_debug,
                        "history": history_debug_dict,
                        "memory": memory_debug_raw,
                    }
                    progress_payload["provider_stats"] = build_provider_stats(chunks0)
                    if retrieval_sources:
                        progress_payload["retrieval_sources"] = retrieval_sources
                    yield f"event: progress\ndata: {_json.dumps(progress_payload)}\n\n"

                    hb = self.chat_service.get_last_history_debug() or {}
                    history_usage = {
                        "builder": hb,
                        "stm": history_debug_dict,
                    }

                    answer_accum: list[str] = []
                    effective_chunks = chunks0
                    extra_system_prompt = None
                    if context_text_for_llm and index_mode == "disabled":
                        context_chunk = {
                            "chunk_id": "context_file",
                            "text": context_text_for_llm,
                            "metadata": {
                                "document_id": "用户上传文档",
                                "page": 1,
                                "source": "uploaded_context",
                                "type": "file_content",
                            },
                        }
                        effective_chunks = [context_chunk] + chunks0
                        extra_system_prompt = (
                            "用户已上传文档作为对话上下文，请基于文档内容回答问题。"
                            if self.chat_service.prompt.language == "zh"
                            else "The user has uploaded documents as conversation context. Please answer based on the document content."
                        )
                        logger.info(
                            "[CONTEXT_FILE_ADDED] session=%s context_text_len=%s chunks_count=%s extra_prompt=%s",
                            session_id,
                            len(context_text_for_llm),
                            len(effective_chunks),
                            extra_system_prompt,
                        )

                    for part in self.chat_service.generate(
                        question=question,
                        chunks=effective_chunks,
                        stream=True,
                        history=history_list,
                        compress_history=compress_history,
                        rolling_summary=s.rolling_summary,
                        extra_system=extra_system_prompt,
                    ):
                        answer_accum.append(part)
                        yield f"data: {part}\n\n"

                    raw_cits = self.rag.build_citations(chunks0)
                    seen = set()
                    citations_tail = []
                    for c in raw_cits:
                        k = (str(c.get("document_id")), str(c.get("page")), str(c.get("chunk_id")))
                        if k in seen:
                            continue
                        seen.add(k)
                        citations_tail.append(c)
                    usage_tail = self.chat_service.get_last_usage() or {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    }
                    debug_tail = {
                        "kb_id": primary_kb_for_debug,
                        "kb_ids": kb_ids_for_debug,
                        "top_k": top_k,
                        "index": idx_override,
                        "index_mode": index_mode,
                    }
                    if retrieval_sources:
                        debug_tail["retrieval_sources"] = retrieval_sources
                    import json as _json
                    debug_tail["history"] = history_usage
                    debug_tail["provider_stats"] = build_provider_stats(chunks0)
                    if not memory_debug_raw.get("disabled"):
                        memory_result = self.memory_service.record_guided_result(
                            session_id=session_id,
                            success=bool((retrieval_debug.get("memory") or {}).get("top_hit")),
                        )
                    else:
                        memory_result = {"success": False, "auto_disabled": True}
                    memory_debug_raw["result"] = memory_result
                    debug_tail["memory"] = memory_debug_raw
                    try:
                        _summary = self.chat_service.get_last_history_summary()
                        if _summary and settings.ENABLE_ROLLING_SUMMARY:
                            SessionService(self.db).update_rolling_summary(
                                session_id=session_id,
                                rolling_summary=_summary,
                            )
                    except Exception:
                        pass
                    completion_payload = {
                        "citations": citations_tail,
                        "usage": usage_tail,
                        "debug": debug_tail,
                        "variant": variant,
                    }
                    try:
                        full_answer = "".join(answer_accum)
                        logger.info(
                            "[STREAM_BEFORE_SAVE] session=%s answer_parts=%s full_answer_len=%s question_len=%s",
                            session_id,
                            len(answer_accum),
                            len(full_answer),
                            len(question),
                        )
                        self.memory_service.record_memories(
                            user_id=self.current_user.id,
                            session_id=session_id,
                            question=question,
                            citations=citations_tail,
                        )
                        retrieval_data = {
                            "citations": citations_tail,
                            "retrieval": retrieval_debug,
                            "memory": memory_debug_raw,
                            "knowledge_base_id": s.knowledge_base_id,
                        }
                        retrieval_data["provider_stats"] = build_provider_stats(chunks0)
                        if retrieval_sources:
                            retrieval_data["retrieval_sources"] = retrieval_sources
                        if context_files:
                            retrieval_data["context_files"] = context_files

                        if replace_from_message:
                            self.db.query(Message).filter(
                                Message.session_id == session_id,
                                Message.create_time >= replace_from_message.create_time,
                            ).delete(synchronize_session=False)
                        msg = Message(
                            session_id=session_id,
                            user_question=question,
                            model_answer=full_answer,
                            retrieval_content=_json.dumps(retrieval_data, ensure_ascii=False),
                        )
                        self.db.add(msg)
                        self.db.commit()
                        logger.info(
                            "[STREAM_SAVE_OK] session=%s question_len=%s answer_len=%s msg_id=%s",
                            session_id,
                            len(question),
                            len(full_answer),
                            msg.message_id,
                        )
                        completion_payload["message_id"] = str(msg.message_id)

                        if context_files:
                            try:
                                s.context_json = None
                                self.db.add(s)
                                self.db.commit()
                                logger.info("[CONTEXT_CLEARED] session=%s", session_id)
                            except Exception as clear_err:
                                logger.warning("Failed to clear context_json: %s", clear_err)
                    except Exception as save_err:
                        logger.error("[STREAM_SAVE_FAIL] session=%s error=%s", session_id, save_err)
                        self.db.rollback()
                    tail = _json.dumps(completion_payload, ensure_ascii=False)
                    yield f"event: completion\ndata: {tail}\n\n"
                except Exception as e:
                    try:
                        logger.error(
                            "ASK stream error user=%s session=%s: %s",
                            self.current_user.id,
                            session_id,
                            e,
                        )
                    except Exception:
                        pass
                    try:
                        import json as _json
                        self.db.add(
                            Message(
                                session_id=session_id,
                                user_question=question,
                                model_answer="",
                                retrieval_content=_json.dumps(
                                    {
                                        "stream_error": True,
                                        "error": str(e),
                                        "retrieval": self.retriever.get_last_retrieval_debug() or {},
                                    },
                                    ensure_ascii=False,
                                ),
                            )
                        )
                        self.db.commit()
                    except Exception:
                        self.db.rollback()
                    yield "event: error\ndata: [Stream Error]\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8")

        history_list, history_debug, query_embedding = self.conversation_service.build_history_slice(
            session_id=session_id,
            question=question,
        )
        history_debug_dict = asdict(history_debug)
        boost_doc_ids, memory_debug_raw = self.conversation_service.fetch_focus_doc_ids(
            user_id=self.current_user.id,
            session=s,
            query=question,
            query_embedding=query_embedding,
        )
        chunks, retrieval_debug, retrieval_sources, idx_override = perform_retrieval(
            question=question,
            top_k=top_k,
            focus_ids=focus_ids,
            boost_ids=boost_doc_ids,
        )

        try:
            reranker = get_reranker()
        except Exception:
            reranker = None
        if reranker and chunks:
            try:
                chunk_models = [
                    RagChunk(
                        chunk_id=item.get("chunk_id", ""),
                        document_id=str((item.get("metadata") or {}).get("document_id", "")),
                        content=item.get("text", ""),
                        metadata=item.get("metadata", {}),
                    )
                    for item in chunks
                ]
                logger.info(
                    "[RERANK_START] query='%s...' chunks_count=%s top_k=%s",
                    question[:60],
                    len(chunk_models),
                    top_k,
                )
                reranked_models = asyncio.run(reranker.rerank(question, chunk_models))  # type: ignore[arg-type]
                chunk_map = {item.get("chunk_id"): item for item in chunks}
                ordered = [chunk_map.get(model.chunk_id) for model in reranked_models if model.chunk_id in chunk_map]
                remaining = [
                    item for item in chunks if item.get("chunk_id") not in {model.chunk_id for model in reranked_models}
                ]
                chunks = ([item for item in ordered if item is not None] + remaining)[:top_k]
                logger.info(
                    "[RERANK_COMPLETE] reranked_count=%s final_chunks=%s",
                    len(reranked_models),
                    len(chunks),
                )
            except Exception as rerank_exc:
                logger.warning(
                    "[RERANK_FAILED] Cross-encoder rerank failed: %s",
                    rerank_exc,
                    exc_info=True,
                )

        try:
            effective_chunks_non_stream = chunks
            extra_system_prompt_non_stream = None
            if context_text_for_llm and index_mode == "disabled":
                context_chunk = {
                    "chunk_id": "context_file",
                    "text": context_text_for_llm,
                    "metadata": {
                        "document_id": "用户上传文档",
                        "page": 1,
                        "source": "uploaded_context",
                        "type": "file_content",
                    },
                }
                effective_chunks_non_stream = [context_chunk] + chunks
                extra_system_prompt_non_stream = (
                    "用户已上传文档作为对话上下文，请基于文档内容回答问题。"
                    if self.chat_service.prompt.language == "zh"
                    else "The user has uploaded documents as conversation context. Please answer based on the document content."
                )

            content = self.chat_service.generate(
                question=question,
                chunks=effective_chunks_non_stream,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
                history=history_list,
                compress_history=compress_history,
                rolling_summary=s.rolling_summary,
                extra_system=extra_system_prompt_non_stream,
            )
        except Exception as e:
            try:
                logger.error(
                    "ASK generate error user=%s session=%s: %s",
                    self.current_user.id,
                    session_id,
                    e,
                )
            except Exception:
                pass
            raise HTTPException(status_code=502, detail="LLM generation failed")

        citations = self.rag.build_citations(chunks)
        usage = self.chat_service.get_last_usage() or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        retrieval_debug = self.retriever.get_last_retrieval_debug() or {}
        history_builder_debug = self.chat_service.get_last_history_debug() or {}
        provider_stats = build_provider_stats(chunks)
        debug = {
            "kb_id": primary_kb_for_debug,
            "kb_ids": kb_ids_for_debug,
            "top_k": top_k,
            "index": idx_override,
            "index_mode": index_mode,
            "variant": variant,
            "retrieval": retrieval_debug,
            "history": {"builder": history_builder_debug, "stm": history_debug_dict},
            "memory": memory_debug_raw,
            "provider_stats": provider_stats,
        }
        if retrieval_sources:
            debug["retrieval_sources"] = retrieval_sources

        self.memory_service.record_memories(
            user_id=self.current_user.id,
            session_id=session_id,
            question=question,
            citations=citations,
        )
        if not memory_debug_raw.get("disabled"):
            memory_result = self.memory_service.record_guided_result(
                session_id=session_id,
                success=bool((retrieval_debug.get("memory") or {}).get("top_hit")),
            )
        else:
            memory_result = {"success": False, "auto_disabled": True}
        memory_debug_raw["result"] = memory_result

        try:
            AskEventLogger().log_event({
                "user_id": str(self.current_user.id),
                "session_id": session_id,
                "kb_id": int(primary_kb_for_debug) if primary_kb_for_debug is not None else None,
                "kb_ids": kb_ids_for_debug,
                "question": str(question)[:512],
                "top_k": int(top_k),
                "strategy": getattr(settings, "SM_RETRIEVAL_STRATEGY", "multi_stage"),
                "hits": len(chunks),
                "retrieval": retrieval_debug,
                "retrieval_sources": retrieval_sources,
                "provider_stats": provider_stats,
                "graph": retrieval_debug.get("graph") or {},
                "citations": citations,
                "usage": usage,
                "answer_chars": len(content or ""),
                "variant": variant,
                "historyUsage": {
                    "builder": history_builder_debug,
                    "stm": history_debug_dict,
                    "compress": bool(compress_history),
                },
                "memory": {"request": memory_debug_raw, "result": memory_result},
                "index": idx_override,
                "index_mode": index_mode,
            })
        except Exception:
            pass

        try:
            _summary = self.chat_service.get_last_history_summary()
            if _summary and settings.ENABLE_ROLLING_SUMMARY:
                self.session_service.update_rolling_summary(
                    session_id=session_id,
                    rolling_summary=_summary,
                )
        except Exception:
            pass

        try:
            retrieval_data_non_stream = {
                "citations": citations,
                "retrieval": retrieval_debug,
                "memory": memory_debug_raw,
                "knowledge_base_id": s.knowledge_base_id,
                "provider_stats": provider_stats,
            }
            if retrieval_sources:
                retrieval_data_non_stream["retrieval_sources"] = retrieval_sources
            if context_files:
                retrieval_data_non_stream["context_files"] = context_files

            if replace_from_message:
                self.db.query(Message).filter(
                    Message.session_id == session_id,
                    Message.create_time >= replace_from_message.create_time,
                ).delete(synchronize_session=False)
            msg = Message(
                session_id=session_id,
                user_question=question,
                model_answer=content,
                retrieval_content=json.dumps(retrieval_data_non_stream, ensure_ascii=False),
            )
            self.db.add(msg)
            self.db.commit()

            if context_files:
                try:
                    s.context_json = None
                    self.db.add(s)
                    self.db.commit()
                    logger.info("[CONTEXT_CLEARED] session=%s", session_id)
                except Exception as clear_err:
                    logger.warning("Failed to clear context_json: %s", clear_err)
        except Exception:
            self.db.rollback()

        return JSONResponse(
            content={
                "answer": content,
                "chunks": chunks,
                "citations": citations,
                "usage": usage,
                "debug": debug,
                "message_id": str(msg.message_id),
            }
        )

    def _get_session(self, session_id: str):
        s = self.session_service.get_session_by_id(session_id=session_id)
        if not s:
            raise HTTPException(status_code=404, detail="会话不存在")
        if str(self.current_user.id) != str(s.user_id):
            raise HTTPException(status_code=403, detail="无权访问该会话")
        return s

    def _resolve_kb_provider(
        self, kb_id: int, *, provider_override: Optional[str] = None
    ) -> tuple[str, Optional[dict]]:
        try:
            kb = knowledgebase_service.get_kb_by_id(
                db=self.db,
                kb_id=int(kb_id),
                user_id=self.current_user.id,
            )
            provider = resolve_provider(provider_override or getattr(kb, "rag_provider", None))
            rag_config = getattr(kb, "rag_config", None)
            return provider, rag_config
        except Exception:
            return resolve_provider(provider_override), None

    def _resolve_replace_message(self, session_id: str, payload: Dict[str, Any]) -> Optional[Message]:
        replace_from_message_id = payload.get("replaceFromMessageId")
        if not replace_from_message_id:
            return None
        try:
            replace_uuid = uuid.UUID(str(replace_from_message_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="replaceFromMessageId 无效") from exc
        replace_from_message = (
            self.db.query(Message)
            .filter(
                Message.session_id == session_id,
                Message.message_id == replace_uuid,
            )
            .first()
        )
        if not replace_from_message:
            raise HTTPException(status_code=404, detail="待替换的历史消息不存在或已被删除")
        return replace_from_message
