"""Chat ask orchestration service."""

from __future__ import annotations

from dataclasses import asdict
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from core.config import settings
from models.message import Message
from models.session import Session as SessionModel
from models.user import User
from schemas.knowledge_base import KnowledgeBaseCreate
from schemas.rag import Chunk as RagChunk
from schemas.session import SessionDefaults
from service.core.components_factory import get_reranker
from service.core.conversation.ask_run_control import get_ask_run_control
from service.core.conversation.ask_stream_replay_buffer import get_ask_stream_replay_buffer
from service.core.conversation.ask_utils import normalize_top_k
from service.core.conversation.routing_decision import (
    classify_query_intent,
    coerce_confidence,
    derive_route_type,
    is_in_rollout,
)
from service.core.conversation.chat_generation_service import ChatGenerationService
from service.core.conversation.conversation_service import ConversationService
from service.core.conversation.deep_research_context_service import DeepResearchContextService
from service.core.rag.retriever import RAGRetriever
from service.core.rag.service import RAGService
from service.core.rag.graph.graph_service import KnowledgeGraphService
from service.core.rag.providers.registry import resolve_provider
from service.core.rag.utils.retrieval_stats import build_provider_stats
from service import knowledgebase_service
from service.memory_service import LongTermMemoryService
from service.session_service import SessionService
from service.core.rag.history.long_term_memory import (
    FactExtractor,
    LongTermMemoryStore,
)
from utils.ask_logger import AskEventLogger
from utils.experiments import assign_variant
from utils.get_logger import logger
from utils.quota import quota
from utils.rate_limiter import rate_limiter
from utils.database import SessionLocal


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
        self.deep_research_context_service = DeepResearchContextService()

    def handle(self, *, session_id: str, payload: Dict[str, Any]) -> JSONResponse | StreamingResponse:
        """Handle a session ask request.

        Args:
            session_id (str): Session identifier.
            payload (Dict[str, Any]): Request payload.

        Returns:
            JSONResponse | StreamingResponse: Response to return from the route.
        """
        s = self._get_session(session_id)
        ask_timeout = float(getattr(settings, "SM_ASK_TIMEOUT_SECS", 120) or 120)
        deadline = time.perf_counter() + ask_timeout if ask_timeout > 0 else None
        question = (payload or {}).get("question") or ""
        requested_run_id = (payload or {}).get("runId")
        if not isinstance(requested_run_id, str):
            requested_run_id = None
        stream = bool((payload or {}).get("stream", True))
        persist_history = bool((payload or {}).get("persistHistory", True))
        compress_history = bool((payload or {}).get("compressHistory", False))
        image_attachments = (
            payload.get("imageAttachments")
            if isinstance(payload.get("imageAttachments"), list)
            else []
        )
        focus_ids = payload.get("focusDocIds") if isinstance(payload.get("focusDocIds"), list) else None
        raw_index_mode = payload.get("indexMode") if isinstance(payload.get("indexMode"), str) else None
        index_mode = raw_index_mode or "auto"
        replace_from_message = (
            self._resolve_replace_message(session_id, payload) if persist_history else None
        )

        bucket = f"ask:{self.current_user.id}:{session_id}"
        ask_rate_limit = max(1, int(getattr(settings, "SM_ASK_RATE_LIMIT_PER_MINUTE", 60) or 60))
        if not rate_limiter.check_and_consume(bucket, limit=ask_rate_limit, window_seconds=60):
            raise HTTPException(status_code=429, detail="Too Many Requests")

        qkey = f"ask:day:{self.current_user.id}:{int(__import__('time').time())//86400}"
        if not quota.consume_count(qkey, settings.DAILY_ASK_COUNT, window_seconds=86400):
            raise HTTPException(status_code=429, detail="Daily ask quota exceeded")

        try:
            logger.info(
                f"[ASK_RECEIVED] user={self.current_user.id} session={session_id} "
                f"q='{str(question)[:80]}' topK={payload.get('topK')} "
                f"index_mode={index_mode} persist_history={persist_history}"
            )
            logger.info(f"[ASK_MODEL_PLAN] {self._build_model_plan_snapshot()}")
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
            if top_k is None and isinstance(defaults_raw.get("topK"), int):
                top_k = defaults_raw.get("topK")
            if temperature is None and isinstance(defaults_raw.get("temperature"), (int, float)):
                temperature = defaults_raw.get("temperature")
            if max_tokens is None and isinstance(defaults_raw.get("maxTokens"), int):
                max_tokens = defaults_raw.get("maxTokens")

        if top_k is None and isinstance(defaults_model.topK, int):
            top_k = defaults_model.topK

        top_k = normalize_top_k(top_k)
        temperature = temperature if isinstance(temperature, (int, float)) else settings.SM_TEMPERATURE
        max_tokens = max_tokens if isinstance(max_tokens, int) else settings.SM_MAX_TOKENS
        provider_override = payload.get("ragProvider") or payload.get("provider")
        if not isinstance(provider_override, str):
            provider_override = defaults_model.retrievalStrategy
        llm_provider_override = payload.get("llmProvider")
        if not isinstance(llm_provider_override, str):
            llm_provider_override = getattr(defaults_model, "llmProvider", None)
        if isinstance(llm_provider_override, str):
            llm_provider_override = llm_provider_override.strip().lower() or None
        llm_model_override = payload.get("llmModel")
        if not isinstance(llm_model_override, str):
            llm_model_override = getattr(defaults_model, "llmModel", None)
        if isinstance(llm_model_override, str):
            llm_model_override = llm_model_override.strip() or None
        try:
            llm_runtime_config = self._normalize_custom_llm_config(payload.get("customLlm"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if llm_runtime_config:
            llm_provider_override = "custom"
            llm_model_override = str(llm_runtime_config.get("model") or llm_model_override or "").strip() or None
        fast_mode = self._is_fast_mode(provider_override)
        enable_rerank = (not fast_mode) or bool(getattr(settings, "SM_FAST_MODE_RERANK_ENABLED", False))

        def _ensure_deadline(stage: str) -> None:
            if deadline is None:
                return
            if time.perf_counter() > deadline:
                raise TimeoutError(f"ASK timeout at {stage}")

        session_kb_id: Optional[int] = int(s.knowledge_base_id) if s.knowledge_base_id is not None else None
        # 会话知识库：仅在会话级开关启用时参与检索
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

        requested_index_mode = raw_index_mode.strip().lower() if isinstance(raw_index_mode, str) else ""
        allowed_modes = {"auto", "session_only", "global_only", "hybrid", "disabled"}
        if requested_index_mode not in allowed_modes:
            requested_index_mode = ""
        if requested_index_mode == "session_only" and session_kb_id is None:
            requested_index_mode = ""
        if requested_index_mode == "disabled" and effective_index_mode != "disabled":
            # 显式禁用检索
            retrieval_plan = []
            effective_index_mode = "disabled"
        index_mode = requested_index_mode or effective_index_mode
        retrieval_disabled = index_mode == "disabled"
        route_reason = "index_mode_enabled"
        if index_mode == "disabled":
            route_reason = "index_mode_disabled"
        elif not retrieval_plan:
            route_reason = "no_retrieval_plan"
        min_confidence = float(
            getattr(settings, "SM_ADAPTIVE_RETRIEVAL_MIN_CONFIDENCE", 0.75) or 0.75
        )
        rollout_percent_raw = getattr(settings, "SM_ADAPTIVE_RETRIEVAL_ROLLOUT_PERCENT", 100)
        try:
            rollout_percent = int(rollout_percent_raw)
        except (TypeError, ValueError):
            rollout_percent = 100
        rollout_key = str(
            getattr(settings, "SM_ADAPTIVE_RETRIEVAL_ROLLOUT_KEY", "adaptive_retrieval_v1")
            or "adaptive_retrieval_v1"
        ).strip() or "adaptive_retrieval_v1"
        adaptive_shadow_enabled = bool(
            getattr(settings, "SM_ADAPTIVE_RETRIEVAL_SHADOW_ENABLED", False)
        )
        rollout_selected, rollout_bucket, rollout_percent = self._is_in_rollout(
            user_id=self.current_user.id,
            session_id=session_id,
            key=rollout_key,
            percent=rollout_percent,
        )
        intent_debug: Dict[str, Any] = {
            "evaluated": False,
            "applied": False,
            "need_retrieval": not retrieval_disabled,
            "query_type": "unknown",
            "confidence": 1.0 if retrieval_disabled else 0.0,
            "threshold": min_confidence,
            "reason": "not_evaluated",
            "policy_version": str(getattr(settings, "SM_LLM_POLICY_VERSION", "v1")),
            "policy_source": "manifest",
            "skip": False,
            "rollout": {
                "enabled": bool(getattr(settings, "SM_ADAPTIVE_RETRIEVAL_ENABLED", False)),
                "key": rollout_key,
                "percent": rollout_percent,
                "bucket": rollout_bucket,
                "selected": rollout_selected,
                "shadow_enabled": adaptive_shadow_enabled,
            },
        }

        # Adaptive retrieval: LLM-based intent classification
        adaptive_base_eligible = (
            not retrieval_disabled
            and requested_index_mode != "disabled"
            and retrieval_plan
            and getattr(settings, "SM_ADAPTIVE_RETRIEVAL_ENABLED", False)
        )
        evaluate_intent = adaptive_base_eligible and (
            rollout_selected or adaptive_shadow_enabled
        )
        if adaptive_base_eligible and not evaluate_intent:
            route_reason = "intent_rollout_holdout"
        if evaluate_intent:
            intent = self._classify_query_intent(question, retrieval_plan)
            intent_need_retrieval = bool(intent.get("need_retrieval", True))
            intent_query_type = str(intent.get("query_type", "unknown"))
            intent_reason = str(intent.get("reason", "llm"))
            intent_confidence_raw = intent.get("confidence", 0.0)
            try:
                intent_confidence = float(intent_confidence_raw)
            except (TypeError, ValueError):
                intent_confidence = 0.0
            intent_confidence = max(0.0, min(1.0, intent_confidence))
            intent_debug = {
                "evaluated": True,
                "applied": rollout_selected,
                "need_retrieval": intent_need_retrieval,
                "query_type": intent_query_type,
                "confidence": intent_confidence,
                "threshold": min_confidence,
                "reason": intent_reason,
                "policy_version": str(
                    intent.get("policy_version")
                    or getattr(settings, "SM_LLM_POLICY_VERSION", "v1")
                ),
                "policy_source": str(intent.get("policy_source") or "manifest"),
                "skip": False,
                "rollout": {
                    "enabled": bool(getattr(settings, "SM_ADAPTIVE_RETRIEVAL_ENABLED", False)),
                    "key": rollout_key,
                    "percent": rollout_percent,
                    "bucket": rollout_bucket,
                    "selected": rollout_selected,
                    "shadow_enabled": adaptive_shadow_enabled,
                },
            }
            skip_by_intent = (
                rollout_selected
                and (not intent_need_retrieval)
                and intent_confidence >= min_confidence
            )
            intent_debug["skip"] = skip_by_intent
            logger.info(
                f"[ADAPTIVE_RETRIEVAL] need_retrieval={intent_need_retrieval} "
                f"query_type={intent_query_type} confidence={intent_confidence:.3f} "
                f"threshold={min_confidence:.3f} reason={intent_reason} "
                f"skip={skip_by_intent} applied={rollout_selected}"
            )
            if skip_by_intent:
                retrieval_disabled = True
                route_reason = "intent_skip"
                logger.info(
                    f"[ADAPTIVE_RETRIEVAL] query_type={intent_query_type} → skip retrieval"
                )
            elif rollout_selected:
                route_reason = (
                    "intent_keep"
                    if intent_need_retrieval
                    else "intent_low_confidence_keep"
                )
            else:
                route_reason = "intent_shadow_observe"

        route_type = self._derive_route_type(index_mode=index_mode, retrieval_disabled=retrieval_disabled)
        route_confidence = self._coerce_confidence(
            intent_debug.get("confidence", 0.0),
            default=1.0,
        )
        if not bool(intent_debug.get("evaluated")):
            route_confidence = 1.0
        route_snapshot: Dict[str, Any] = {
            "type": route_type,
            "reason": route_reason,
            "confidence": route_confidence,
            "policy_version": intent_debug.get("policy_version"),
            "policy_source": intent_debug.get("policy_source"),
            "retrieval_disabled": retrieval_disabled,
            "requested_index_mode": requested_index_mode or "auto",
            "resolved_index_mode": index_mode,
            "effective_index_mode": effective_index_mode,
            "plan": [
                {"scope": scope, "kb_id": kb_id}
                for scope, kb_id in retrieval_plan
            ],
            "intent": intent_debug,
        }
        logger.info(
            f"[ROUTE_DECISION] session={session_id} route_type={route_type} "
            f"reason={route_reason} confidence={route_confidence:.3f} "
            f"retrieval_disabled={retrieval_disabled} index_mode={index_mode} "
            f"plan={route_snapshot['plan']}"
        )

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
                if label == "session":
                    mode_override = index_mode
                else:
                    mode_override = "global_only"
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
                        f"[CONTEXT_JSON_LOADED] session={session_id} "
                        f"files_count={len(uploaded_files)} "
                        f"context_text_len={len(context_text_for_llm)}"
                    )
            except Exception as ctx_err:
                logger.warning(f"Failed to parse context_json: {ctx_err}")

        t_deep_research_context = time.perf_counter()
        deep_research_context_text, deep_research_context_debug = (
            self.deep_research_context_service.build_context_text(
                session_id=session_id,
                user_id=int(self.current_user.id),
            )
        )
        deep_research_context_ms = int((time.perf_counter() - t_deep_research_context) * 1000)
        deep_research_extra_system = None
        if deep_research_context_text:
            deep_research_extra_system = (
                "补充上下文：以下是本会话既有深度研究的结果摘要（仅结果，非过程轨迹）。\n"
                + deep_research_context_text
                if self.chat_service.prompt.language == "zh"
                else "Additional context: prior DeepResearch result summaries from this chat session "
                "(result-only, no process traces).\n"
                + deep_research_context_text
            )

        ltm_facts_extra_system = None
        ltm_facts_debug_raw: Dict[str, Any] = {"enabled": False}
        try:
            ltm_segment, ltm_debug = self.conversation_service.recall_ltm_facts(
                user_id=self.current_user.id,
                question=question,
                language=self.chat_service.prompt.language or "zh",
            )
            ltm_facts_debug_raw = {
                "enabled": ltm_debug.enabled,
                "facts_retrieved": ltm_debug.facts_retrieved,
                "elapsed_ms": ltm_debug.elapsed_ms,
                "details": ltm_debug.details,
            }
            if ltm_segment:
                ltm_facts_extra_system = ltm_segment
        except Exception as ltm_recall_exc:
            logger.debug(f"LTM recall failed (non-fatal): {ltm_recall_exc}")

        def compose_extra_system(*segments: Optional[str]) -> Optional[str]:
            merged = [str(item).strip() for item in segments if isinstance(item, str) and item.strip()]
            if not merged:
                return None
            return "\n\n".join(merged)

        if stream:
            def gen():
                nonlocal retrieval_disabled
                run_id = self._resolve_run_id(requested_run_id)
                replay_buffer = None
                run_control = None
                try:
                    import json as _json
                    seq = 0
                    replay_buffer = get_ask_stream_replay_buffer()
                    replay_buffer.create_run(
                        run_id=run_id,
                        session_id=session_id,
                        user_id=int(self.current_user.id),
                    )
                    run_control = get_ask_run_control()
                    run_control.register_run(
                        run_id=run_id,
                        session_id=session_id,
                        user_id=int(self.current_user.id),
                    )

                    def _build_event(event_type: str, payload_data: Dict[str, Any]) -> str:
                        nonlocal seq
                        seq += 1
                        base = {
                            "version": "v1",
                            "type": event_type,
                            "seq": seq,
                            "run_id": run_id,
                        }
                        base.update(payload_data or {})
                        return _json.dumps(base, ensure_ascii=False)

                    def _emit(event_type: str, payload_data: Dict[str, Any]) -> str:
                        payload_json = _build_event(event_type, payload_data)
                        replay_buffer.append_event(
                            run_id=run_id,
                            seq=seq,
                            frame=f"event: {event_type}\ndata: {payload_json}\n\n",
                        )
                        return f"event: {event_type}\ndata: {payload_json}\n\n"

                    def _cancelled() -> bool:
                        return bool(run_control.is_cancelled(run_id))

                    def _cancel_frames(stage: str) -> List[str]:
                        cancel_message = "已取消当前请求"
                        return [
                            _emit(
                                "progress",
                                {
                                    "stage": "cancelled",
                                    "index_mode": index_mode,
                                    "message": f"{cancel_message}（{stage}）",
                                },
                            ),
                            _emit(
                                "completion",
                                {
                                    "cancelled": True,
                                    "persisted": False,
                                    "answer": "",
                                    "citations": [],
                                    "usage": {
                                        "prompt_tokens": 0,
                                        "completion_tokens": 0,
                                        "total_tokens": 0,
                                    },
                                    "message_id": str(uuid.uuid4()),
                                },
                            ),
                        ]

                    t_start = time.perf_counter()
                    history_ms = None
                    memory_ms = None
                    retrieval_ms = None
                    rerank_ms = None
                    generation_ms = None
                    yield _emit(
                        "progress",
                        {
                            "stage": "accepted",
                            "index_mode": index_mode,
                            "route_type": route_type,
                            "route_reason": route_reason,
                            "route_confidence": route_confidence,
                            "retrieval_disabled": retrieval_disabled,
                            "message": (
                                "请求已接收，正在准备回答"
                                if retrieval_disabled
                                else "请求已接收，正在准备检索"
                            ),
                        },
                    )
                    yield _emit(
                        "progress",
                        {
                            "stage": "history",
                            "index_mode": index_mode,
                            "route_type": route_type,
                            "route_reason": route_reason,
                            "route_confidence": route_confidence,
                            "retrieval_disabled": retrieval_disabled,
                            "message": "正在构建会话上下文",
                        },
                    )
                    if _cancelled():
                        for frame in _cancel_frames("history"):
                            yield frame
                        return
                    t_history = time.perf_counter()
                    history_list, history_debug, query_embedding = self.conversation_service.build_history_slice(
                        session_id=session_id,
                        question=question,
                        enable_semantic=not retrieval_disabled,
                    )
                    history_ms = int((time.perf_counter() - t_history) * 1000)
                    history_debug_dict = asdict(history_debug)
                    yield _emit(
                        "progress",
                        {
                            "stage": "memory",
                            "index_mode": index_mode,
                            "route_type": route_type,
                            "route_reason": route_reason,
                            "route_confidence": route_confidence,
                            "retrieval_disabled": retrieval_disabled,
                            "message": (
                                "RAG 已关闭，跳过知识库记忆引导"
                                if retrieval_disabled
                                else "正在处理记忆引导"
                            ),
                        },
                    )
                    if _cancelled():
                        for frame in _cancel_frames("memory"):
                            yield frame
                        return
                    t_memory = time.perf_counter()
                    if retrieval_disabled:
                        boost_doc_ids = []
                        memory_debug_raw = {
                            "disabled": True,
                            "reason": "retrieval_disabled",
                            "fail_count": s.memory_guide_fail_count or 0,
                            "candidates": [],
                            "selected": [],
                        }
                    else:
                        boost_doc_ids, memory_debug_raw = self.conversation_service.fetch_focus_doc_ids(
                            user_id=self.current_user.id,
                            session=s,
                            query=question,
                            query_embedding=query_embedding,
                        )
                    memory_ms = int((time.perf_counter() - t_memory) * 1000)
                    _ensure_deadline("after_memory")
                    yield _emit(
                        "progress",
                        {
                            "stage": "retrieving",
                            "index_mode": index_mode,
                            "route_type": route_type,
                            "route_reason": route_reason,
                            "route_confidence": route_confidence,
                            "retrieval_disabled": retrieval_disabled,
                            "message": (
                                "正在分析问题并组织上下文，请稍候"
                                if retrieval_disabled
                                else "正在检索知识库，请稍候"
                            ),
                        },
                    )
                    if _cancelled():
                        for frame in _cancel_frames("retrieving"):
                            yield frame
                        return
                    should_retrieve_stream = bool((not retrieval_disabled) and retrieval_plan)
                    if should_retrieve_stream:
                        t_retrieval = time.perf_counter()
                        chunks0, retrieval_debug, retrieval_sources, idx_override = perform_retrieval(
                            question=question,
                            top_k=top_k,
                            focus_ids=focus_ids,
                            boost_ids=boost_doc_ids,
                        )
                        retrieval_ms = int((time.perf_counter() - t_retrieval) * 1000)
                    else:
                        chunks0 = []
                        retrieval_debug = {
                            "disabled": True,
                            "reason": (
                                "intent_skip"
                                if route_reason == "intent_skip"
                                else "retrieval_disabled"
                            ),
                            "execution_chain": "disabled",
                        }
                        retrieval_sources = {}
                        idx_override = "disabled"
                        retrieval_ms = 0
                    _ensure_deadline("after_retrieval")
                    reranker_stream = None
                    rerank_init_error: Optional[str] = None
                    rerank_status_stream: Dict[str, Any] = {
                        "enabled": bool(enable_rerank),
                        "executed": False,
                        "backend": "disabled" if not enable_rerank else "unknown",
                        "success": None,
                        "fallback_used": False,
                        "reason": "disabled" if not enable_rerank else "not_run",
                        "elapsed_ms": None,
                    }
                    if should_retrieve_stream and enable_rerank:
                        try:
                            reranker_stream = get_reranker()
                        except Exception as rerank_init_exc:
                            reranker_stream = None
                            rerank_init_error = str(rerank_init_exc)
                    if should_retrieve_stream and enable_rerank and reranker_stream and chunks0:
                        try:
                            t_rerank = time.perf_counter()
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
                                f"[RERANK_START_STREAM] query='{question[:60]}...' chunks_count={len(stream_models)} top_k={top_k}"
                            )
                            reranked_stream = reranker_stream.rerank_sync(question, stream_models)
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
                            rerank_ms = int((time.perf_counter() - t_rerank) * 1000)
                            rerank_status_stream.update(self._get_rerank_status_snapshot(reranker_stream))
                            rerank_status_stream.update(
                                {
                                    "executed": True,
                                    "input_chunks": len(stream_models),
                                    "output_chunks": len(chunks0),
                                    "elapsed_ms": rerank_ms,
                                }
                            )
                            logger.info(
                                f"[RERANK_COMPLETE_STREAM] reranked_count={len(reranked_stream)} "
                                f"final_chunks={len(chunks0)} status={rerank_status_stream}"
                            )
                        except Exception as rerank_exc:
                            rerank_status_stream.update(self._get_rerank_status_snapshot(reranker_stream))
                            rerank_status_stream.update(
                                {
                                    "executed": True,
                                    "success": False,
                                    "reason": f"exception:{rerank_exc}",
                                    "input_chunks": len(chunks0),
                                    "output_chunks": len(chunks0),
                                    "elapsed_ms": rerank_ms,
                                }
                            )
                            logger.exception(
                                f"[RERANK_FAILED_STREAM] err={rerank_exc} status={rerank_status_stream}"
                            )
                    elif should_retrieve_stream and enable_rerank and not reranker_stream:
                        rerank_status_stream.update(
                            {
                                "backend": "unavailable",
                                "success": False,
                                "reason": f"init_failed:{rerank_init_error}" if rerank_init_error else "init_failed",
                            }
                        )
                    elif should_retrieve_stream and enable_rerank and not chunks0:
                        rerank_status_stream.update(
                            {
                                "backend": "skipped",
                                "success": True,
                                "reason": "no_candidates",
                            }
                        )
                    elif not should_retrieve_stream:
                        rerank_status_stream.update(
                            {
                                "backend": "skipped",
                                "success": True,
                                "reason": "retrieval_disabled",
                            }
                        )
                    if _cancelled():
                        for frame in _cancel_frames("rerank"):
                            yield frame
                        return
                    _ensure_deadline("after_rerank")
                    chunks0, rag_gate_pass = self._apply_relevance_gate(chunks0)
                    if not rag_gate_pass:
                        retrieval_disabled = True
                    progress_payload = {
                        "stage": "retrieved",
                        "hits": len(chunks0),
                        "index": idx_override,
                        "index_mode": index_mode,
                        "route_type": route_type,
                        "route_reason": route_reason,
                        "route_confidence": route_confidence,
                        "retrieval_disabled": retrieval_disabled,
                        "message": (
                            "上下文准备完成，开始生成回答"
                            if retrieval_disabled
                            else "检索完成，开始生成回答"
                        ),
                    }
                    yield _emit("progress", progress_payload)

                    hb = self.chat_service.get_last_history_debug() or {}
                    history_usage = {
                        "builder": hb,
                        "stm": history_debug_dict,
                    }

                    answer_accum: list[str] = []
                    stream_parse_buffer = ""
                    stream_in_reasoning = False
                    stream_tag_guard = max(
                        len("<think>"),
                        len("</think>"),
                        len("<reasoning>"),
                        len("</reasoning>"),
                    )

                    def _find_next_tag(text: str, tags: Tuple[str, ...]) -> Optional[Tuple[int, str]]:
                        lowered = text.lower()
                        best_idx = -1
                        best_tag = ""
                        for tag in tags:
                            idx = lowered.find(tag)
                            if idx < 0:
                                continue
                            if best_idx < 0 or idx < best_idx:
                                best_idx = idx
                                best_tag = tag
                        if best_idx < 0:
                            return None
                        return best_idx, best_tag

                    def _consume_stream_part(part_text: str, *, flush: bool = False) -> List[Tuple[str, str]]:
                        nonlocal stream_parse_buffer, stream_in_reasoning
                        emitted: List[Tuple[str, str]] = []
                        if part_text:
                            stream_parse_buffer += str(part_text)
                        while stream_parse_buffer:
                            tags: Tuple[str, ...] = (
                                ("</think>", "</reasoning>")
                                if stream_in_reasoning
                                else ("<think>", "<reasoning>")
                            )
                            next_tag = _find_next_tag(stream_parse_buffer, tags)
                            if next_tag:
                                idx, tag = next_tag
                                segment = stream_parse_buffer[:idx]
                                if segment:
                                    emitted.append(
                                        ("reasoning" if stream_in_reasoning else "answer", segment)
                                    )
                                stream_parse_buffer = stream_parse_buffer[idx + len(tag):]
                                stream_in_reasoning = not stream_in_reasoning
                                continue
                            if flush:
                                emitted.append(
                                    ("reasoning" if stream_in_reasoning else "answer", stream_parse_buffer)
                                )
                                stream_parse_buffer = ""
                                break
                            keep = min(len(stream_parse_buffer), max(stream_tag_guard - 1, 0))
                            flush_upto = len(stream_parse_buffer) - keep
                            if flush_upto <= 0:
                                break
                            segment = stream_parse_buffer[:flush_upto]
                            if segment:
                                emitted.append(
                                    ("reasoning" if stream_in_reasoning else "answer", segment)
                                )
                            stream_parse_buffer = stream_parse_buffer[flush_upto:]
                            break
                        return emitted

                    runtime_model_emitted = False
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
                            f"[CONTEXT_FILE_ADDED] session={session_id} context_text_len={len(context_text_for_llm)} "
                            f"chunks_count={len(effective_chunks)}"
                        )
                    extra_system_prompt = compose_extra_system(
                        extra_system_prompt,
                        deep_research_extra_system,
                        ltm_facts_extra_system,
                    )

                    t_generate = time.perf_counter()
                    cancelled_during_generation = False
                    for part in self.chat_service.generate(
                        question=question,
                        chunks=effective_chunks,
                        stream=True,
                        history=history_list,
                        compress_history=compress_history,
                        rolling_summary=s.rolling_summary,
                        extra_system=extra_system_prompt,
                        llm_model=llm_model_override,
                        llm_provider=llm_provider_override,
                        llm_runtime_config=llm_runtime_config,
                        image_attachments=image_attachments,
                        rag_mode=not retrieval_disabled,
                    ):
                        if _cancelled():
                            cancelled_during_generation = True
                            break
                        _ensure_deadline("generation")
                        if not runtime_model_emitted:
                            runtime_model = self.chat_service.get_last_runtime_model()
                            if runtime_model:
                                runtime_model_emitted = True
                                yield _emit("runtime_model", runtime_model)
                        part_text = ""
                        part_channel = ""
                        if isinstance(part, dict):
                            part_text = str(part.get("content") or "")
                            part_channel = str(part.get("channel") or "").strip().lower()
                            if part_channel in {"thinking", "thought"}:
                                part_channel = "reasoning"
                        else:
                            part_text = str(part or "")
                        if not part_text:
                            continue
                        if part_channel == "reasoning":
                            yield _emit(
                                "reasoning_delta",
                                {"content": part_text, "thinking": True, "channel": "reasoning"},
                            )
                            continue
                        if part_channel == "answer":
                            answer_accum.append(part_text)
                            yield _emit("delta", {"content": part_text, "channel": "answer"})
                            continue
                        for channel, segment in _consume_stream_part(part_text):
                            if not segment:
                                continue
                            if channel == "reasoning":
                                yield _emit(
                                    "reasoning_delta",
                                    {"content": segment, "thinking": True, "channel": "reasoning"},
                                )
                                continue
                            answer_accum.append(segment)
                            yield _emit("delta", {"content": segment, "channel": "answer"})
                    for channel, segment in _consume_stream_part("", flush=True):
                        if not segment:
                            continue
                        if channel == "reasoning":
                            yield _emit(
                                "reasoning_delta",
                                {"content": segment, "thinking": True, "channel": "reasoning"},
                            )
                            continue
                        answer_accum.append(segment)
                        yield _emit("delta", {"content": segment, "channel": "answer"})
                    generation_ms = int((time.perf_counter() - t_generate) * 1000)
                    if cancelled_during_generation and not answer_accum:
                        for frame in _cancel_frames("generation"):
                            yield frame
                        return

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
                    runtime_model_tail = self.chat_service.get_last_runtime_model()
                    debug_tail = {
                        "kb_id": primary_kb_for_debug,
                        "kb_ids": kb_ids_for_debug,
                        "top_k": top_k,
                        "index": idx_override,
                        "index_mode": index_mode,
                        "route": route_snapshot,
                        "timing": {
                            "deep_research_context_ms": deep_research_context_ms,
                            "history_ms": history_ms,
                            "memory_ms": memory_ms,
                            "retrieval_ms": retrieval_ms,
                            "rerank_ms": rerank_ms,
                            "generation_ms": generation_ms,
                            "total_ms": int((time.perf_counter() - t_start) * 1000),
                        },
                    }
                    if retrieval_sources:
                        debug_tail["retrieval_sources"] = retrieval_sources
                    import json as _json
                    debug_tail["history"] = history_usage
                    debug_tail["provider_stats"] = build_provider_stats(chunks0)
                    debug_tail["rerank"] = rerank_status_stream
                    debug_tail["deep_research_context"] = deep_research_context_debug
                    memory_success = bool((retrieval_debug.get("memory") or {}).get("top_hit"))
                    if persist_history and not memory_debug_raw.get("disabled"):
                        self._record_memory_guide_async(
                            session_id=session_id,
                            success=memory_success,
                        )
                        memory_result = {
                            "success": memory_success,
                            "auto_disabled": None,
                            "pending": True,
                        }
                    else:
                        memory_result = {"success": False, "auto_disabled": True}
                    memory_debug_raw["result"] = memory_result
                    debug_tail["memory"] = memory_debug_raw
                    try:
                        logger.info(
                            f"[ASK_PIPELINE_STREAM] session={session_id} variant={variant} "
                            f"index_mode={index_mode} route_type={route_type} route_reason={route_reason} "
                            f"route_confidence={route_confidence:.3f} "
                            f"chain={(retrieval_debug or {}).get('execution_chain')} "
                            f"hits={len(chunks0)} deep_research_context_ms={deep_research_context_ms} "
                            f"history_ms={history_ms} memory_ms={memory_ms} retrieval_ms={retrieval_ms} rerank_ms={rerank_ms} "
                            f"generation_ms={generation_ms} total_ms={debug_tail['timing']['total_ms']} "
                            f"rerank_status={rerank_status_stream}"
                        )
                    except Exception:
                        pass
                    try:
                        _summary = self.chat_service.get_last_history_summary()
                        if persist_history and _summary and settings.ENABLE_ROLLING_SUMMARY:
                            SessionService(self.db).update_rolling_summary(
                                session_id=session_id,
                                rolling_summary=_summary,
                            )
                    except Exception:
                        pass
                    message_uuid = uuid.uuid4()
                    completion_payload = {
                        "type": "completion",
                        "citations": citations_tail,
                        "usage": usage_tail,
                        "runtime_model": runtime_model_tail,
                        "route": route_snapshot,
                        "cancelled": bool(cancelled_during_generation),
                        "message_id": str(message_uuid),
                        "persisted": False,
                        "answer": "",
                    }
                    try:
                        raw_answer = "".join(answer_accum)
                        # 工业级 RAG 引用契约的最终化（参考 Perplexity / NotebookLM）：
                        #   1) 归一化 + 越界过滤 + 元注释剥离；
                        #   2) 提取真正被 [N] 引用过的 chunk，按出现顺序重新编号；
                        #   3) 裁剪 citations 数组——右侧引文面板只展示真正支撑回答
                        #      的来源，避免「召回了但没用上」的低质量 chunk（纯 URL、
                        #      纯标题、reference 节）污染面板。
                        # 兜底：LLM 一个 [N] 都没用 → 保留全部 citations，与
                        # 截图里那种「只有 URL/标题」的引文一样退化但不消失，
                        # 至少让用户感知系统检索到了内容。
                        try:
                            full_answer, citations_tail, finalize_meta = (
                                self.chat_service.finalize_answer_with_citations(
                                    raw_answer, citations_tail
                                )
                            )
                        except Exception:
                            full_answer = raw_answer
                            finalize_meta = {"used": 0, "total": len(citations_tail), "fallback_all": True}
                        completion_payload["answer"] = full_answer
                        completion_payload["citations"] = citations_tail
                        debug_tail["citation_finalize"] = finalize_meta
                        logger.info(
                            f"[STREAM_BEFORE_SAVE] session={session_id} answer_parts={len(answer_accum)} "
                            f"full_answer_len={len(full_answer)} question_len={len(question)} "
                            f"citations_used={finalize_meta.get('used')} citations_total={finalize_meta.get('total')} "
                            f"citations_dropped={finalize_meta.get('dropped', 0)}"
                        )
                        if persist_history:
                            self._record_memories_async(
                                user_id=self.current_user.id,
                                session_id=session_id,
                                question=question,
                                citations=citations_tail,
                            )
                            self._extract_and_store_ltm_facts_async(
                                user_id=self.current_user.id,
                                session_id=session_id,
                                question=question,
                                answer=full_answer,
                            )
                            retrieval_data = {
                                "citations": citations_tail,
                                "retrieval": retrieval_debug,
                                "memory": memory_debug_raw,
                                "ltm_facts": ltm_facts_debug_raw,
                                "rerank": rerank_status_stream,
                                "route": route_snapshot,
                                "timing": debug_tail.get("timing"),
                                "knowledge_base_id": s.knowledge_base_id,
                                "usage": usage_tail,
                                "llm_model": (runtime_model_tail or {}).get("actual_model") or llm_model_override,
                                "llm_provider": (runtime_model_tail or {}).get("actual_provider") or llm_provider_override,
                                "runtime_model": runtime_model_tail,
                                "cancelled": bool(cancelled_during_generation),
                                "deep_research_context": deep_research_context_debug,
                            }
                            if image_attachments:
                                retrieval_data["image_attachments"] = image_attachments
                            retrieval_data["provider_stats"] = build_provider_stats(chunks0)
                            if retrieval_sources:
                                retrieval_data["retrieval_sources"] = retrieval_sources
                            if context_files:
                                retrieval_data["context_files"] = context_files

                            replace_time = replace_from_message.create_time if replace_from_message else None
                            retrieval_json = _json.dumps(retrieval_data, ensure_ascii=False)
                            self._persist_message_sync(
                                message_id=message_uuid,
                                session_id=session_id,
                                question=question,
                                answer=full_answer,
                                retrieval_content=retrieval_json,
                                replace_from_time=replace_time,
                                clear_context=bool(context_files),
                            )
                            completion_payload["persisted"] = True
                            logger.info(
                                f"[STREAM_SAVE_SYNC] session={session_id} question_len={len(question)} "
                                f"answer_len={len(full_answer)} msg_id={message_uuid}"
                            )
                    except Exception as save_err:
                        logger.error(f"[STREAM_SAVE_FAIL] session={session_id} error={save_err}")
                        self.db.rollback()
                    AskEventLogger().log_event({
                        "user_id": str(self.current_user.id),
                        "session_id": session_id,
                        "kb_id": int(primary_kb_for_debug) if primary_kb_for_debug is not None else None,
                        "kb_ids": kb_ids_for_debug,
                        "question": str(question)[:512],
                        "top_k": int(top_k),
                        "strategy": getattr(settings, "SM_RETRIEVAL_STRATEGY", "multi_stage"),
                        "hits": len(chunks0),
                        "retrieval": retrieval_debug,
                        "retrieval_sources": retrieval_sources,
                        "provider_stats": build_provider_stats(chunks0),
                        "graph": retrieval_debug.get("graph") or {},
                        "citations": completion_payload.get("citations") or [],
                        "usage": usage_tail,
                        "answer_chars": len(str(completion_payload.get("answer") or "")),
                        "variant": variant,
                        "route_type": route_type,
                        "route_reason": route_reason,
                        "route_confidence": route_confidence,
                        "route": route_snapshot,
                        "timing": debug_tail.get("timing"),
                        "rerank": rerank_status_stream,
                        "historyUsage": {
                            "builder": history_usage.get("builder"),
                            "stm": history_usage.get("stm"),
                            "compress": bool(compress_history),
                        },
                        "memory": {"request": memory_debug_raw, "result": memory_result},
                        "deepResearchContext": deep_research_context_debug,
                        "index": idx_override,
                        "index_mode": index_mode,
                        "stream": True,
                        "persisted": bool(completion_payload.get("persisted")),
                        "cancelled": bool(completion_payload.get("cancelled")),
                    })
                    yield _emit("completion", completion_payload)
                except Exception as e:
                    try:
                        logger.exception(
                            f"ASK stream error user={self.current_user.id} "
                            f"session={session_id}: {type(e).__name__}: {e}"
                        )
                    except Exception:
                        pass
                    yield _emit(
                        "error",
                        {
                            "message": str(e) or "Stream Error",
                            "code": "ask_stream_failed",
                        },
                    )
                finally:
                    if replay_buffer is not None:
                        replay_buffer.mark_completed(run_id)
                    if run_control is not None:
                        run_control.clear_run(run_id)

            return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8")

        t_start = time.perf_counter()
        history_ms = None
        memory_ms = None
        retrieval_ms = None
        rerank_ms = None
        generation_ms = None

        t_history = time.perf_counter()
        history_list, history_debug, query_embedding = self.conversation_service.build_history_slice(
            session_id=session_id,
            question=question,
            enable_semantic=not retrieval_disabled,
        )
        history_ms = int((time.perf_counter() - t_history) * 1000)
        history_debug_dict = asdict(history_debug)
        t_memory = time.perf_counter()
        if retrieval_disabled:
            boost_doc_ids = []
            memory_debug_raw = {
                "disabled": True,
                "reason": "retrieval_disabled",
                "fail_count": s.memory_guide_fail_count or 0,
                "candidates": [],
                "selected": [],
            }
        else:
            boost_doc_ids, memory_debug_raw = self.conversation_service.fetch_focus_doc_ids(
                user_id=self.current_user.id,
                session=s,
                query=question,
                query_embedding=query_embedding,
            )
        memory_ms = int((time.perf_counter() - t_memory) * 1000)
        try:
            _ensure_deadline("after_memory")
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        should_retrieve_non_stream = bool((not retrieval_disabled) and retrieval_plan)
        if should_retrieve_non_stream:
            t_retrieval = time.perf_counter()
            chunks, retrieval_debug, retrieval_sources, idx_override = perform_retrieval(
                question=question,
                top_k=top_k,
                focus_ids=focus_ids,
                boost_ids=boost_doc_ids,
            )
            retrieval_ms = int((time.perf_counter() - t_retrieval) * 1000)
        else:
            chunks = []
            retrieval_debug = {
                "disabled": True,
                "reason": (
                    "intent_skip"
                    if route_reason == "intent_skip"
                    else "retrieval_disabled"
                ),
                "execution_chain": "disabled",
            }
            retrieval_sources = {}
            idx_override = "disabled"
            retrieval_ms = 0
        try:
            _ensure_deadline("after_retrieval")
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc

        reranker = None
        rerank_init_error: Optional[str] = None
        rerank_status: Dict[str, Any] = {
            "enabled": bool(enable_rerank),
            "executed": False,
            "backend": "disabled" if not enable_rerank else "unknown",
            "success": None,
            "fallback_used": False,
            "reason": "disabled" if not enable_rerank else "not_run",
            "elapsed_ms": None,
        }
        if should_retrieve_non_stream and enable_rerank:
            try:
                reranker = get_reranker()
            except Exception as rerank_init_exc:
                reranker = None
                rerank_init_error = str(rerank_init_exc)
        if should_retrieve_non_stream and enable_rerank and reranker and chunks:
            try:
                t_rerank = time.perf_counter()
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
                    f"[RERANK_START] query='{question[:60]}...' chunks_count={len(chunk_models)} top_k={top_k}"
                )
                reranked_models = reranker.rerank_sync(question, chunk_models)
                chunk_map = {item.get("chunk_id"): item for item in chunks}
                ordered = [chunk_map.get(model.chunk_id) for model in reranked_models if model.chunk_id in chunk_map]
                remaining = [
                    item for item in chunks if item.get("chunk_id") not in {model.chunk_id for model in reranked_models}
                ]
                chunks = ([item for item in ordered if item is not None] + remaining)[:top_k]
                rerank_ms = int((time.perf_counter() - t_rerank) * 1000)
                rerank_status.update(self._get_rerank_status_snapshot(reranker))
                rerank_status.update(
                    {
                        "executed": True,
                        "input_chunks": len(chunk_models),
                        "output_chunks": len(chunks),
                        "elapsed_ms": rerank_ms,
                    }
                )
                logger.info(
                    f"[RERANK_COMPLETE] reranked_count={len(reranked_models)} final_chunks={len(chunks)} "
                    f"status={rerank_status}"
                )
            except Exception as rerank_exc:
                rerank_status.update(self._get_rerank_status_snapshot(reranker))
                rerank_status.update(
                    {
                        "executed": True,
                        "success": False,
                        "reason": f"exception:{rerank_exc}",
                        "input_chunks": len(chunks),
                        "output_chunks": len(chunks),
                        "elapsed_ms": rerank_ms,
                    }
                )
                logger.exception(
                    f"[RERANK_FAILED] err={rerank_exc} status={rerank_status}"
                )
        elif should_retrieve_non_stream and enable_rerank and not reranker:
            rerank_status.update(
                {
                    "backend": "unavailable",
                    "success": False,
                    "reason": f"init_failed:{rerank_init_error}" if rerank_init_error else "init_failed",
                }
            )
        elif should_retrieve_non_stream and enable_rerank and not chunks:
            rerank_status.update(
                {
                    "backend": "skipped",
                    "success": True,
                    "reason": "no_candidates",
                }
            )
        elif not should_retrieve_non_stream:
            rerank_status.update(
                {
                    "backend": "skipped",
                    "success": True,
                    "reason": "retrieval_disabled",
                }
            )
        try:
            _ensure_deadline("after_rerank")
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc

        chunks, rag_gate_pass_ns = self._apply_relevance_gate(chunks)
        if not rag_gate_pass_ns:
            retrieval_disabled = True

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
            extra_system_prompt_non_stream = compose_extra_system(
                extra_system_prompt_non_stream,
                deep_research_extra_system,
                ltm_facts_extra_system,
            )

            try:
                _ensure_deadline("before_generation")
            except TimeoutError as exc:
                raise HTTPException(status_code=504, detail=str(exc)) from exc
            t_generate = time.perf_counter()
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
                llm_model=llm_model_override,
                llm_provider=llm_provider_override,
                llm_runtime_config=llm_runtime_config,
                image_attachments=image_attachments,
                rag_mode=not retrieval_disabled,
            )
            generation_ms = int((time.perf_counter() - t_generate) * 1000)
        except Exception as e:
            try:
                logger.error(
                    f"[ASK_GENERATE_FAIL] user={self.current_user.id} session={session_id} err={e}"
                )
            except Exception:
                pass
            raise HTTPException(status_code=502, detail="LLM generation failed")

        # 引用契约最终化：与流式分支保持一致——重编号、裁剪只剩真正引用过的来源。
        citations = self.rag.build_citations(chunks)
        try:
            content, citations, _finalize_meta = (
                self.chat_service.finalize_answer_with_citations(content or "", citations)
            )
        except Exception:
            pass
        usage = self.chat_service.get_last_usage() or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if not bool((retrieval_debug or {}).get("disabled")):
            retrieval_debug = self.retriever.get_last_retrieval_debug() or retrieval_debug
        history_builder_debug = self.chat_service.get_last_history_debug() or {}
        provider_stats = build_provider_stats(chunks)
        debug = {
            "kb_id": primary_kb_for_debug,
            "kb_ids": kb_ids_for_debug,
            "top_k": top_k,
            "index": idx_override,
            "index_mode": index_mode,
            "route": route_snapshot,
            "variant": variant,
            "retrieval": retrieval_debug,
            "history": {"builder": history_builder_debug, "stm": history_debug_dict},
            "memory": memory_debug_raw,
            "rerank": rerank_status,
            "deep_research_context": deep_research_context_debug,
            "provider_stats": provider_stats,
            "timing": {
                "deep_research_context_ms": deep_research_context_ms,
                "history_ms": history_ms,
                "memory_ms": memory_ms,
                "retrieval_ms": retrieval_ms,
                "rerank_ms": rerank_ms,
                "generation_ms": generation_ms,
                "total_ms": int((time.perf_counter() - t_start) * 1000),
            },
        }
        if retrieval_sources:
            debug["retrieval_sources"] = retrieval_sources
        try:
            logger.info(
                f"[ASK_PIPELINE_NON_STREAM] session={session_id} variant={variant} "
                f"index_mode={index_mode} route_type={route_type} route_reason={route_reason} "
                f"route_confidence={route_confidence:.3f} "
                f"chain={(retrieval_debug or {}).get('execution_chain')} "
                f"hits={len(chunks)} deep_research_context_ms={deep_research_context_ms} "
                f"history_ms={history_ms} memory_ms={memory_ms} retrieval_ms={retrieval_ms} rerank_ms={rerank_ms} "
                f"generation_ms={generation_ms} total_ms={debug['timing']['total_ms']} "
                f"rerank_status={rerank_status}"
            )
        except Exception:
            pass

        if persist_history:
            self._record_memories_async(
                user_id=self.current_user.id,
                session_id=session_id,
                question=question,
                citations=citations,
            )
            self._extract_and_store_ltm_facts_async(
                user_id=self.current_user.id,
                session_id=session_id,
                question=question,
                answer=answer,
            )
        memory_success = bool((retrieval_debug.get("memory") or {}).get("top_hit"))
        if persist_history and not memory_debug_raw.get("disabled"):
            self._record_memory_guide_async(
                session_id=session_id,
                success=memory_success,
            )
            memory_result = {
                "success": memory_success,
                "auto_disabled": None,
                "pending": True,
            }
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
                "route_type": route_type,
                "route_reason": route_reason,
                "route_confidence": route_confidence,
                "route": route_snapshot,
                "timing": debug.get("timing"),
                "rerank": rerank_status,
                "historyUsage": {
                    "builder": history_builder_debug,
                    "stm": history_debug_dict,
                    "compress": bool(compress_history),
                },
                "memory": {"request": memory_debug_raw, "result": memory_result},
                "deepResearchContext": deep_research_context_debug,
                "index": idx_override,
                "index_mode": index_mode,
                "stream": False,
            })
        except Exception:
            pass

        try:
            _summary = self.chat_service.get_last_history_summary()
            if persist_history and _summary and settings.ENABLE_ROLLING_SUMMARY:
                self.session_service.update_rolling_summary(
                    session_id=session_id,
                    rolling_summary=_summary,
                )
        except Exception:
            pass

        response_message_id = str(uuid.uuid4())
        persisted_success = False
        try:
            retrieval_data_non_stream = {
                "citations": citations,
                "retrieval": retrieval_debug,
                "memory": memory_debug_raw,
                "rerank": rerank_status,
                "route": route_snapshot,
                "timing": debug.get("timing"),
                "knowledge_base_id": s.knowledge_base_id,
                "provider_stats": provider_stats,
                "usage": usage,
                "llm_model": llm_model_override,
                "llm_provider": llm_provider_override,
                "deep_research_context": deep_research_context_debug,
            }
            if image_attachments:
                retrieval_data_non_stream["image_attachments"] = image_attachments
            if retrieval_sources:
                retrieval_data_non_stream["retrieval_sources"] = retrieval_sources
            if context_files:
                retrieval_data_non_stream["context_files"] = context_files

            if persist_history:
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
                response_message_id = str(msg.message_id)
                persisted_success = True

                if context_files:
                    try:
                        s.context_json = None
                        self.db.add(s)
                        self.db.commit()
                        logger.info(f"[CONTEXT_CLEARED] session={session_id}")
                    except Exception as clear_err:
                        logger.warning(f"Failed to clear context_json: {clear_err}")
        except Exception:
            self.db.rollback()

        return JSONResponse(
            content={
                "answer": content,
                "chunks": chunks,
                "citations": citations,
                "usage": usage,
                "debug": debug,
                "message_id": response_message_id,
                "persisted": persisted_success,
            }
        )

    def replay_stream(
        self,
        *,
        session_id: str,
        run_id: str,
        since_seq: int = -1,
    ) -> StreamingResponse:
        """Replay ask SSE events by run_id + seq."""
        # 权限校验：会话必须属于当前用户
        self._get_session(session_id)
        replay_buffer = get_ask_stream_replay_buffer()
        run_state = replay_buffer.get_run(run_id)
        if not run_state:
            run_control = get_ask_run_control()
            pending_run = run_control.get_run(run_id)
            if (
                pending_run
                and bool(pending_run.cancelled)
                and str(pending_run.session_id) == str(session_id)
                and int(pending_run.user_id) == int(self.current_user.id)
            ):
                def cancelled_gen():
                    progress_payload = {
                        "version": "v1",
                        "type": "progress",
                        "seq": 1,
                        "run_id": run_id,
                        "stage": "cancelled",
                        "message": "已取消当前请求",
                    }
                    completion_payload = {
                        "version": "v1",
                        "type": "completion",
                        "seq": 2,
                        "run_id": run_id,
                        "cancelled": True,
                        "persisted": False,
                        "answer": "",
                        "citations": [],
                        "usage": {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                        },
                        "message_id": str(uuid.uuid4()),
                    }
                    yield (
                        "event: progress\n"
                        f"data: {json.dumps(progress_payload, ensure_ascii=False)}\n\n"
                    )
                    yield (
                        "event: completion\n"
                        f"data: {json.dumps(completion_payload, ensure_ascii=False)}\n\n"
                    )

                return StreamingResponse(
                    cancelled_gen(),
                    media_type="text/event-stream; charset=utf-8",
                )
            raise HTTPException(status_code=404, detail="run_id 不存在或已过期")
        if str(run_state.session_id) != str(session_id):
            raise HTTPException(status_code=404, detail="run_id 不属于该会话")
        if str(run_state.user_id) != str(self.current_user.id):
            raise HTTPException(status_code=403, detail="无权访问该 run_id")

        def gen():
            yield from replay_buffer.stream_from(
                run_id=run_id,
                since_seq=int(since_seq),
            )

        return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8")

    def cancel_run(self, *, session_id: str, run_id: str) -> Dict[str, Any]:
        """Cancel an active ask stream by run_id."""
        self._get_session(session_id)
        run_control = get_ask_run_control()
        cancelled = run_control.cancel_run(
            run_id=run_id,
            session_id=session_id,
            user_id=int(self.current_user.id),
        )
        return {
            "run_id": run_id,
            "cancelled": bool(cancelled),
        }

    def _resolve_run_id(self, requested_run_id: Optional[str]) -> str:
        raw = str(requested_run_id or "").strip()
        if raw:
            try:
                _ = uuid.UUID(raw)
                return raw
            except Exception:
                pass
        return str(uuid.uuid4())

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
        logger.info(
            f"Backfilled session KB id={kb.id} for ask session {session_obj.session_id}"
        )

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

    def _is_fast_mode(self, provider: Optional[str]) -> bool:
        provider_norm = (provider or "").strip().lower()
        return provider_norm not in {"graph", "multimodal_graph"}

    def _get_rerank_status_snapshot(self, reranker: Any) -> Dict[str, Any]:
        getter = getattr(reranker, "get_last_status", None)
        if callable(getter):
            try:
                status = getter()
                if isinstance(status, dict):
                    return dict(status)
            except Exception:
                pass
        return {
            "backend": type(reranker).__name__ if reranker is not None else "none",
            "success": None,
            "fallback_used": False,
            "reason": "unknown",
            "elapsed_ms": None,
            "cooldown": False,
        }

    def _build_model_plan_snapshot(self) -> Dict[str, Any]:
        answer_llm = getattr(self.chat_service, "llm", None)
        summary_llm = getattr(self.conversation_service, "llm_client", None)
        aux_llm = getattr(self.rag, "llm_aux", None)
        graph_llm = getattr(getattr(self.graph_service, "extractor", None), "llm", None)
        return {
            "answer_model": getattr(answer_llm, "model", None),
            "summary_model": getattr(summary_llm, "model", None),
            "aux_model": getattr(aux_llm, "model", None),
            "graph_model": getattr(graph_llm, "model", None),
            "reranker_type": getattr(settings, "SM_RERANKER_TYPE", None),
            "reranker_dashscope_model": getattr(settings, "SM_DASHSCOPE_RERANK_MODEL", None),
            "embedding_model": getattr(settings, "SM_EMBEDDING_MODEL", None),
        }

    def _record_memory_guide_async(
        self,
        *,
        session_id: str,
        success: bool,
    ) -> None:
        def _worker() -> None:
            db = SessionLocal()
            try:
                service = LongTermMemoryService(db)
                service.record_guided_result(
                    session_id=session_id,
                    success=success,
                )
            except Exception as exc:
                try:
                    logger.warning(f"Memory guide async update failed: {exc}")
                except Exception:
                    pass
                try:
                    db.rollback()
                except Exception:
                    pass
            finally:
                try:
                    db.close()
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def _record_memories_async(
        self,
        *,
        user_id: int | str,
        session_id: str,
        question: str,
        citations: List[Dict[str, object]],
    ) -> None:
        if not citations:
            return

        def _worker() -> None:
            db = SessionLocal()
            try:
                LongTermMemoryService(db).record_memories(
                    user_id=user_id,
                    session_id=session_id,
                    question=question,
                    citations=citations,
                )
                db.commit()
            except Exception as exc:
                try:
                    logger.warning(f"LTM async record failed: {exc}")
                except Exception:
                    pass
                try:
                    db.rollback()
                except Exception:
                    pass
            finally:
                try:
                    db.close()
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def _extract_and_store_ltm_facts_async(
        self,
        *,
        user_id: int | str,
        session_id: str,
        question: str,
        answer: str,
    ) -> None:
        """Asynchronously extract facts from a completed turn and store them in ES."""

        def _worker() -> None:
            try:
                language = getattr(self.chat_service.prompt, "language", "zh") or "zh"
                extractor = FactExtractor(language=language)
                facts = extractor.extract(question=question, answer=answer)
                if not facts:
                    return
                store = LongTermMemoryStore()
                stored = store.store_facts(
                    user_id=str(user_id),
                    session_id=str(session_id),
                    facts=facts,
                )
                if stored:
                    logger.info(
                        f"LTM facts stored: user={user_id} session={session_id} "
                        f"extracted={len(facts)} stored={stored}"
                    )
            except Exception as exc:
                try:
                    logger.warning(f"LTM fact extraction/storage failed: {exc}")
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def _persist_message_async(
        self,
        *,
        message_id: uuid.UUID,
        session_id: str,
        question: str,
        answer: str,
        retrieval_content: str,
        replace_from_time,
        clear_context: bool,
    ) -> None:
        def _worker() -> None:
            db = SessionLocal()
            try:
                max_retries = 2
                for attempt in range(max_retries + 1):
                    try:
                        if replace_from_time:
                            db.query(Message).filter(
                                Message.session_id == session_id,
                                Message.create_time >= replace_from_time,
                            ).delete(synchronize_session=False)
                        msg = Message(
                            message_id=message_id,
                            session_id=session_id,
                            user_question=question,
                            model_answer=answer,
                            retrieval_content=retrieval_content,
                        )
                        db.add(msg)
                        if clear_context:
                            s = (
                                db.query(SessionModel)
                                .filter(SessionModel.session_id == session_id)
                                .first()
                            )
                            if s:
                                s.context_json = None
                        db.commit()
                        return
                    except Exception as exc:
                        try:
                            logger.warning(
                                f"Async message persist failed (attempt {attempt + 1}): {exc}"
                            )
                        except Exception:
                            pass
                        try:
                            db.rollback()
                        except Exception:
                            pass
                        if attempt < max_retries:
                            try:
                                import time
                                time.sleep(0.2)
                            except Exception:
                                pass
            finally:
                try:
                    db.close()
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def _persist_message_sync(
        self,
        *,
        message_id: uuid.UUID,
        session_id: str,
        question: str,
        answer: str,
        retrieval_content: str,
        replace_from_time,
        clear_context: bool,
    ) -> None:
        """Persist message synchronously to guarantee history consistency on completion."""
        try:
            if replace_from_time:
                self.db.query(Message).filter(
                    Message.session_id == session_id,
                    Message.create_time >= replace_from_time,
                ).delete(synchronize_session=False)
            msg = Message(
                message_id=message_id,
                session_id=session_id,
                user_question=question,
                model_answer=answer,
                retrieval_content=retrieval_content,
            )
            self.db.add(msg)
            if clear_context:
                s = (
                    self.db.query(SessionModel)
                    .filter(SessionModel.session_id == session_id)
                    .first()
                )
                if s:
                    s.context_json = None
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    @staticmethod
    def _normalize_custom_llm_config(raw: Any) -> Optional[Dict[str, Any]]:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("customLlm must be an object")
        provider_type = str(raw.get("providerType") or "").strip().lower()
        if provider_type != "openai_compatible":
            raise ValueError("customLlm.providerType must be openai_compatible")
        base_url = str(raw.get("baseUrl") or "").strip().rstrip("/")
        model_name = str(raw.get("model") or "").strip()
        api_key = str(raw.get("apiKey") or "").strip()
        if not base_url:
            raise ValueError("customLlm.baseUrl is required")
        if not model_name:
            raise ValueError("customLlm.model is required")
        if not api_key:
            raise ValueError("customLlm.apiKey is required")
        if not (base_url.startswith("http://") or base_url.startswith("https://")):
            raise ValueError("customLlm.baseUrl must start with http:// or https://")
        provider_label = str(raw.get("providerLabel") or "").strip() or "Custom"
        allow_fallback = bool(raw.get("allowFallback"))
        return {
            "provider_type": "openai_compatible",
            "provider_label": provider_label,
            "base_url": base_url,
            "api_key": api_key,
            "model": model_name,
            "allow_fallback": allow_fallback,
        }

    @staticmethod
    def _coerce_confidence(value: Any, *, default: float = 0.0) -> float:
        return coerce_confidence(value, default=default)

    @staticmethod
    def _derive_route_type(*, index_mode: str, retrieval_disabled: bool) -> str:
        return derive_route_type(index_mode=index_mode, retrieval_disabled=retrieval_disabled)

    @staticmethod
    def _is_in_rollout(
        *,
        user_id: str | int,
        session_id: str,
        key: str,
        percent: int,
    ) -> tuple[bool, int, int]:
        return is_in_rollout(
            user_id=user_id,
            session_id=session_id,
            key=key,
            percent=percent,
        )

    @staticmethod
    def _apply_relevance_gate(
        chunks: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], bool]:
        """Check top chunk score against threshold; return (chunks, rag_mode_override).

        If the best score is below threshold, return empty chunks + rag_mode=False
        so the generation falls back to chat mode without injecting low-quality context.
        """
        if not getattr(settings, "SM_RELEVANCE_GATE_ENABLED", False):
            return chunks, True

        if not chunks:
            return chunks, False

        threshold = float(getattr(settings, "SM_RELEVANCE_GATE_THRESHOLD", 0.3) or 0.3)

        def _best_score(chunk: Dict[str, Any]) -> float:
            md = chunk.get("metadata") or {}
            for key in ("rerank_score", "retrieval_score", "fused_score"):
                val = md.get(key)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        continue
            try:
                return float(chunk.get("score") or 0.0)
            except (TypeError, ValueError):
                return 0.0

        top_score = max(_best_score(c) for c in chunks)
        if top_score < threshold:
            try:
                logger.info(
                    f"[RELEVANCE_GATE] top_score={top_score:.4f} "
                    f"< threshold={threshold:.4f} → fallback to chat mode"
                )
            except Exception:
                pass
            return [], False

        return chunks, True

    @staticmethod
    def _classify_query_intent(
        question: str,
        retrieval_plan: list[tuple[str, int]],
    ) -> Dict[str, Any]:
        return classify_query_intent(
            question=question,
            retrieval_plan=retrieval_plan,
        )
