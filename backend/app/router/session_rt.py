import asyncio

from dataclasses import asdict
import json
import os
import uuid
from typing import List as _List, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Query, Body
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session

from core.config import settings
from models.message import Message
from models.user import User
from schemas.knowledge_base import KnowledgeBaseCreate
from schemas.rag import Chunk as RagChunk
from schemas.session import CreateSessionRequest, CreateSessionResponse, SessionDefaults, SessionDetail, CompareRequest, CompareResponse
from service import document_service as _doc_svc
from service.auth import get_current_user
from service.core.api.utils.file_storage import FileStorageUtil
from service.core.components_factory import get_reranker
from service.core.rag.history import ShortTermMemoryBuilder
from service.core.rag.service import RAGService
from service.job_handler.local_upload_handler import LocalUploadHandler
from service.job_runner_service import execute_job
from service.job_service import job_service
from service.knowledgebase_service import create_kb_for_user, get_kb_by_id
from service.memory_service import LongTermMemoryService
from service.session_service import SessionService
from utils.ask_logger import AskEventLogger
from utils.database import get_db
from utils.experiments import assign_variant
from utils.get_logger import logger
from utils.quota import quota
from utils.rate_limiter import rate_limiter

router = APIRouter()
@router.get("/{session_id}/messages", summary="分页获取会话完整历史")
def list_messages(
    session_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SessionService(db)
    s = svc.get_session_by_id(session_id=session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    if str(current_user.id) != str(s.user_id):
        raise HTTPException(status_code=403, detail="无权访问该会话")

    q = db.query(Message).filter(Message.session_id == session_id)
    total = q.count()
    items = (
        q.order_by(Message.create_time.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    out = [
        {
            "message_id": str(m.message_id),
            "session_id": m.session_id,
            "user_question": m.user_question,
            "model_answer": m.model_answer,
            "create_time": str(m.create_time),
        }
        for m in items
    ]
    return {"total": total, "page": page, "pageSize": page_size, "items": out}


@router.post("/", response_model=CreateSessionResponse)
def create_session(
    req: CreateSessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    创建新会话（安全：会话表新增列为可空，先校验再持久化）。

    - 临时会话: ephemeral=True 时，创建临时知识库；
    - 绑定会话: 提供 kbId 时校验归属；
    - 两者必须至少满足其一。
    """
    # 会话表主键 String(16)，保持前缀"session_"(8) + 8位hex，总长正好16
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    session_svc = SessionService(db)

    if req.ephemeral:
        kb_name = f"temp_kb_for_{session_id}"
        kb = create_kb_for_user(
            db=db,
            kb_create=KnowledgeBaseCreate(name=kb_name, description=None, is_ephemeral=True),
            user_id=current_user.id,
        )
        kb_id = kb.id
        logger.info(f"Created ephemeral KB id={kb_id} for session {session_id}")
    elif req.kbId:
        kb = get_kb_by_id(db=db, kb_id=req.kbId, user_id=current_user.id)
        kb_id = kb.id
        logger.info(f"Bind session {session_id} to existing KB id={kb_id}")
    else:
        raise HTTPException(
            status_code=400,
            detail="必须提供 kbId 或将 ephemeral 设为 true。",
        )

    defaults = req.defaults or SessionDefaults()

    session_svc.create_session(
        session_id=session_id,
        user_id=current_user.id,
        knowledge_base_id=kb_id,
        session_name=f"Session for KB {kb_id}",
        defaults_json=json.dumps(defaults.model_dump(), ensure_ascii=False),
    )

    return CreateSessionResponse(
        sessionId=session_id,
        kbId=kb_id,
        ephemeral=req.ephemeral,
        defaults=defaults,
    )


@router.post("/{session_id}/create-and-upload", summary="一步创建会话并上传（可选复用已有会话）")
def create_and_upload(
    session_id: Optional[str] = None,
    files: _List[UploadFile] = File(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = None,
):
    """若未传 session_id 则创建临时会话并绑定临时 KB，然后上传。
    若传入 session_id 则复用其 KB 直接上传。"""
    if not session_id:
        req = CreateSessionRequest(kbId=None, ephemeral=True, defaults=None)
        resp = create_session(req, current_user=current_user, db=db)
        session_id = resp.sessionId

    return upload_by_session(
        session_id=session_id,
        background_tasks=background_tasks,
        files=files,
        file_single=file,
        db=db,
        current_user=current_user,
    )


@router.get("/{session_id}", response_model=SessionDetail)
def get_session_detail(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SessionService(db)
    s = svc.get_session_by_id(session_id=session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    if str(current_user.id) != str(s.user_id):
        raise HTTPException(status_code=403, detail="无权访问该会话")
    return SessionDetail(sessionId=s.session_id, kbId=s.knowledge_base_id, sessionName=s.session_name)


@router.get("/{session_id}/defaults", response_model=SessionDefaults)
def get_session_defaults(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SessionService(db)
    s = svc.get_session_by_id(session_id=session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    if str(current_user.id) != str(s.user_id):
        raise HTTPException(status_code=403, detail="无权访问该会话")
    if s.defaults_json:
        try:
            data = json.loads(s.defaults_json)
            return SessionDefaults(**data)
        except Exception:
            pass
    return SessionDefaults()


@router.put("/{session_id}/defaults", response_model=SessionDefaults)
def update_session_defaults(
    session_id: str,
    payload: SessionDefaults,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SessionService(db)
    s = svc.get_session_by_id(session_id=session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    if str(current_user.id) != str(s.user_id):
        raise HTTPException(status_code=403, detail="无权访问该会话")
    svc.update_defaults_json(session_id=session_id, defaults_json=json.dumps(payload.model_dump(), ensure_ascii=False))
    return payload


@router.post("/{session_id}/upload", summary="基于会话的本地上传（异步）")
def upload_by_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    files: _List[UploadFile] = File(None),
    file_single: UploadFile | None = File(None, alias="file"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """根据 sessionId 解析到 kbId，并复用现有的本地上传流程创建后台任务。"""
    session_svc = SessionService(db)
    s = session_svc.get_session_by_id(session_id=session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    if str(current_user.id) != str(s.user_id):
        raise HTTPException(status_code=403, detail="无权操作该会话")
    if not s.knowledge_base_id:
        raise HTTPException(status_code=400, detail="该会话未绑定知识库，无法上传")

    kb_id = s.knowledge_base_id

    up_files: _List[UploadFile] = []
    if file_single is not None:
        up_files.append(file_single)
    if files:
        up_files.extend(files)
    if not up_files:
        raise HTTPException(status_code=400, detail="No files provided")

    allowed_exts = {".pdf", ".docx", ".txt"}
    invalid = [f.filename for f in up_files if f and f.filename and (not any(f.filename.lower().endswith(ext) for ext in allowed_exts))]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unsupported file types: {', '.join(invalid)}")

    metas = []
    errors = []
    for f in up_files:
        try:
            metas.append(FileStorageUtil.save_upload_temp_session(f, current_user.id, session_id))
        except ValueError as ve:
            errors.append({"filename": f.filename, "error": str(ve)})
        except Exception:
            errors.append({"filename": f.filename, "error": "save failed"})

    if metas and errors:
        pass
    if not metas and errors:
        raise HTTPException(status_code=413, detail={"message": "All files rejected", "errors": errors})

    job = job_service.create_job(
        db,
        user_id=current_user.id,
        kb_id=kb_id,
        type="UPLOAD_LOCAL",
        payload={"files": metas, "precheckErrors": errors, "sessionId": session_id},
    )

    background_tasks.add_task(
        execute_job,
        job_id=job.id,
        handler_cls=LocalUploadHandler,
    )

    return job


@router.get("/{session_id}/retrieve", response_model=list[RagChunk], summary="最小检索验证")
def retrieve_by_session(
    session_id: str,
    q: str = Query(..., description="查询文本"),
    top_k: int = Query(5, ge=1, le=50),
    focus_doc_ids: Optional[str] = Query(None, description="以逗号分隔的 document_id 列表"),
    use_session_index: bool = Query(False, description="是否使用会话级临时索引"),
    index_mode: Optional[str] = Query(None, description="索引检索模式: auto/session_only/global_only/hybrid"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SessionService(db)
    s = svc.get_session_by_id(session_id=session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    if str(current_user.id) != str(s.user_id):
        raise HTTPException(status_code=403, detail="无权访问该会话")
    if not s.knowledge_base_id:
        raise HTTPException(status_code=400, detail="该会话未绑定知识库")

    # defaults override
    if s.defaults_json:
        try:
            d = json.loads(s.defaults_json)
            if isinstance(d, dict) and isinstance(d.get("topK"), int):
                top_k = d.get("topK") or top_k
        except Exception:
            pass

    session_index = f"sm_sess_{session_id}"
    idx_override = session_index
    effective_mode = index_mode if isinstance(index_mode, str) else ("session_only" if use_session_index else "global_only")
    focus_ids_list = None
    if focus_doc_ids:
        try:
            focus_ids_list = [int(x) for x in focus_doc_ids.split(",") if x.strip().isdigit()]
        except Exception:
            focus_ids_list = None

    rag = RAGService()
    stm_builder = ShortTermMemoryBuilder(db)
    memory_service = LongTermMemoryService(db)
    results = rag.retrieve(
        query=q,
        kb_id=int(s.knowledge_base_id),
        top_k=top_k,
        focus_doc_ids=focus_ids_list,
        session_index=session_index,
        index_mode=effective_mode,
    )

    out: list[RagChunk] = []
    for item in results:
        md = item.get("metadata") or {}
        out.append(
            RagChunk(
                chunk_id=str(item.get("chunk_id", "")),
                document_id=str(md.get("document_id", "")),
                content=item.get("text", ""),
                metadata=md,
            )
        )
    return out


@router.delete("/{session_id}", summary="删除会话并清理临时资源")
def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SessionService(db)
    s = svc.get_session_by_id(session_id=session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    if str(current_user.id) != str(s.user_id):
        raise HTTPException(status_code=403, detail="无权操作该会话")

    # 文件清理（第三波会扩展到会话专属 tmp 与索引清理）
    # 这里暂只返回 200，避免误删 KB 资源
    return {"deleted": True}


@router.post("/{session_id}/ask", summary="RAG 基础问答（流式/非流式）")
def ask(
    session_id: str,
    payload: dict = Body(..., description="{ question: string, stream?: boolean, focusDocIds?: number[], topK?: number, temperature?: number, maxTokens?: number, compressHistory?: boolean }"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SessionService(db)
    s = svc.get_session_by_id(session_id=session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    if str(current_user.id) != str(s.user_id):
        raise HTTPException(status_code=403, detail="无权访问该会话")
    if not s.knowledge_base_id:
        raise HTTPException(status_code=400, detail="该会话未绑定知识库")

    question = (payload or {}).get("question") or ""
    stream = bool((payload or {}).get("stream", True))
    compress_history = bool((payload or {}).get("compressHistory", False))
    focus_ids = payload.get("focusDocIds") if isinstance(payload.get("focusDocIds"), list) else None
    raw_index_mode = payload.get("indexMode") if isinstance(payload.get("indexMode"), str) else None
    index_mode = raw_index_mode or "auto"

    # basic rate limit: per (user, session) 60 req/min
    bucket = f"ask:{current_user.id}:{session_id}"
    if not rate_limiter.check_and_consume(bucket, limit=60, window_seconds=60):
        raise HTTPException(status_code=429, detail="Too Many Requests")

    # daily ask quota per user
    qkey = f"ask:day:{current_user.id}:{int(__import__('time').time())//86400}"
    if not quota.consume_count(qkey, settings.DAILY_ASK_COUNT, window_seconds=86400):
        raise HTTPException(status_code=429, detail="Daily ask quota exceeded")

    # audit (dev placeholder)
    try:
        logger.info(f"ASK user={current_user.id} session={session_id} q='{str(question)[:80]}' topK={payload.get('topK')}")
    except Exception:
        pass

    # defaults override
    top_k = payload.get("topK") if isinstance(payload.get("topK"), int) else None
    temperature = payload.get("temperature") if isinstance(payload.get("temperature"), (int, float)) else None
    max_tokens = payload.get("maxTokens") if isinstance(payload.get("maxTokens"), int) else None

    if s.defaults_json:
        try:
            d = json.loads(s.defaults_json)
            if top_k is None and isinstance(d.get("topK"), int):
                top_k = d.get("topK")
            if temperature is None and isinstance(d.get("temperature"), (int, float)):
                temperature = d.get("temperature")
            if max_tokens is None and isinstance(d.get("maxTokens"), int):
                max_tokens = d.get("maxTokens")
        except Exception:
            pass

    top_k = top_k if isinstance(top_k, int) and 1 <= top_k <= 50 else settings.SM_RAG_TOPK
    temperature = temperature if isinstance(temperature, (int, float)) else settings.SM_TEMPERATURE
    max_tokens = max_tokens if isinstance(max_tokens, int) else settings.SM_MAX_TOKENS

    rag = RAGService()
    stm_builder = ShortTermMemoryBuilder(db)
    memory_service = LongTermMemoryService(db)
    # 实验分流（稳定一致）
    variant = assign_variant(user_id=current_user.id, session_id=session_id, key="ask_mq_rrf", buckets=("A","B"))

    if stream:
        def gen():
            try:
                history_list, history_debug, query_embedding = stm_builder.build_history(
                    session_id=session_id,
                    question=question,
                )
                history_debug_dict = asdict(history_debug)
                boost_doc_ids, memory_debug_raw = memory_service.fetch_focus_doc_ids(
                    user_id=current_user.id,
                    session=s,
                    query=question,
                    query_embedding=query_embedding,
                )
                session_index = f"sm_sess_{session_id}"
                idx_override = session_index
                idx_override = session_index
                # 先检索，立即告知客户端检索完成，减少“无响应”体感
                chunks0 = rag.retrieve(
                    query=question,
                    kb_id=int(s.knowledge_base_id),
                    top_k=top_k,
                    focus_doc_ids=focus_ids,
                    boost_doc_ids=boost_doc_ids,
                    session_index=session_index,
                    index_mode=index_mode,
                )
                try:
                    reranker_stream = get_reranker()
                except Exception:
                    reranker_stream = None
                if reranker_stream and chunks0:
                    try:
                        top_stream = int(getattr(settings, "SM_L2_RERANK_TOPK", len(chunks0)) or len(chunks0))
                        stream_candidates = chunks0[:top_stream]
                        stream_models = [
                            RagChunk(
                                chunk_id=item.get("chunk_id", ""),
                                document_id=str((item.get("metadata") or {}).get("document_id", "")),
                                content=item.get("text", ""),
                                metadata=item.get("metadata", {}),
                            )
                            for item in stream_candidates
                        ]
                        reranked_stream = asyncio.run(reranker_stream.rerank(question, stream_models))  # type: ignore[arg-type]
                        chunk_stream_map = {item.get("chunk_id"): item for item in chunks0}
                        ordered_stream = [chunk_stream_map.get(model.chunk_id) for model in reranked_stream if model.chunk_id in chunk_stream_map]
                        remaining_stream = [item for item in chunks0 if item.get("chunk_id") not in {model.chunk_id for model in reranked_stream}]
                        chunks0 = [item for item in ordered_stream if item is not None] + remaining_stream
                    except Exception as rerank_exc:
                        logger.warning(f"Cross-encoder rerank failed (stream): {rerank_exc}")
                import json as _json
                retrieval_debug = rag.get_last_retrieval_debug() or {}
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
                yield f"event: progress\ndata: {_json.dumps(progress_payload)}\n\n"

                hb = rag.get_last_history_debug() or {}
                history_usage = {
                    "builder": hb,
                    "stm": history_debug_dict,
                }

                answer_accum: list[str] = []
                for part in rag.generate(question=question, chunks=chunks0, stream=True, history=history_list, compress_history=compress_history, rolling_summary=s.rolling_summary):
                    answer_accum.append(part)
                    yield f"data: {part}\n\n"
                # stream tail: attach citations/usage/debug
                # 对 citations 进行一次轻量去重：按 (document_id,page,chunk_id)
                raw_cits = rag.build_citations(chunks0)
                seen = set()
                citations_tail = []
                for c in raw_cits:
                    k = (str(c.get("document_id")), str(c.get("page")), str(c.get("chunk_id")))
                    if k in seen:
                        continue
                    seen.add(k)
                    citations_tail.append(c)
                usage_tail = rag.get_last_usage() or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                debug_tail = {
                    "kb_id": s.knowledge_base_id,
                    "top_k": top_k,
                    "index": idx_override,
                    "index_mode": index_mode,
                }
                import json as _json
                debug_tail["history"] = history_usage
                if not memory_debug_raw.get("disabled"):
                    memory_result = memory_service.record_guided_result(
                        session_id=session_id,
                        success=bool((retrieval_debug.get("memory") or {}).get("top_hit")),
                    )
                else:
                    memory_result = {"success": False, "auto_disabled": True}
                memory_debug_raw["result"] = memory_result
                debug_tail["memory"] = memory_debug_raw
                # 持久化滚动摘要（若生成且开关开启）
                try:
                    _summary = rag.get_last_history_summary()
                    if _summary and settings.ENABLE_ROLLING_SUMMARY:
                        from service.session_service import SessionService as _SS
                        _SS(db).update_rolling_summary(session_id=session_id, rolling_summary=_summary)
                except Exception:
                    pass
                tail = _json.dumps({"citations": citations_tail, "usage": usage_tail, "debug": debug_tail, "variant": variant}, ensure_ascii=False)
                # 持久化本轮问答（聚合后的答案）
                try:
                    full_answer = "".join(answer_accum)
                    memory_service.record_memories(
                        user_id=current_user.id,
                        session_id=session_id,
                        question=question,
                        citations=citations_tail,
                    )
                    db.add(
                        Message(
                            session_id=session_id,
                            user_question=question,
                            model_answer=full_answer,
                            retrieval_content=_json.dumps({
                                "citations": citations_tail,
                                "retrieval": retrieval_debug,
                                "memory": memory_debug_raw,
                            }, ensure_ascii=False),
                        )
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                yield f"event: completion\ndata: {tail}\n\n"
            except Exception as e:
                try:
                    logger.error(f"ASK stream error user={current_user.id} session={session_id}: {e}")
                except Exception:
                    pass
                # 记录流式异常（便于排障）
                try:
                    import json as _json
                    db.add(
                        Message(
                            session_id=session_id,
                            user_question=question,
                            model_answer="",
                            retrieval_content=_json.dumps({
                                "stream_error": True,
                                "error": str(e),
                                "retrieval": rag.get_last_retrieval_debug() or {},
                            }, ensure_ascii=False),
                        )
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                yield f"event: error\ndata: [Stream Error]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8")

    # non-streaming
    session_index = f"sm_sess_{session_id}"
    idx_override = session_index
    history_list, history_debug, query_embedding = stm_builder.build_history(
        session_id=session_id,
        question=question,
    )
    history_debug_dict = asdict(history_debug)
    boost_doc_ids, memory_debug_raw = memory_service.fetch_focus_doc_ids(
        user_id=current_user.id,
        session=s,
        query=question,
        query_embedding=query_embedding,
    )
    chunks = rag.retrieve(
        query=question,
        kb_id=int(s.knowledge_base_id),
        top_k=top_k,
        focus_doc_ids=focus_ids,
        boost_doc_ids=boost_doc_ids,
        session_index=session_index,
        index_mode=index_mode,
    )

    # L2 Cross-Encoder rerank
    try:
        reranker = get_reranker()
    except Exception:
        reranker = None
    if reranker and chunks:
        try:
            top_for_rerank = int(getattr(settings, "SM_L2_RERANK_TOPK", len(chunks)) or len(chunks))
            rerank_candidates = chunks[:top_for_rerank]
            chunk_models = [
                RagChunk(
                    chunk_id=item.get("chunk_id", ""),
                    document_id=str((item.get("metadata") or {}).get("document_id", "")),
                    content=item.get("text", ""),
                    metadata=item.get("metadata", {}),
                )
                for item in rerank_candidates
            ]
            reranked_models = asyncio.run(reranker.rerank(question, chunk_models))  # type: ignore[arg-type]
            chunk_map = {item.get("chunk_id"): item for item in chunks}
            ordered = [chunk_map.get(model.chunk_id) for model in reranked_models if model.chunk_id in chunk_map]
            remaining = [item for item in chunks if item.get("chunk_id") not in {model.chunk_id for model in reranked_models}]
            chunks = [item for item in ordered if item is not None] + remaining
        except Exception as rerank_exc:
            logger.warning(f"Cross-encoder rerank failed: {rerank_exc}")

    try:
        content = rag.generate(question=question, chunks=chunks, temperature=temperature, max_tokens=max_tokens, stream=False, history=history_list, compress_history=compress_history, rolling_summary=s.rolling_summary)
    except Exception as e:
        try:
            logger.error(f"ASK generate error user={current_user.id} session={session_id}: {e}")
        except Exception:
            pass
        raise HTTPException(status_code=502, detail="LLM generation failed")
    citations = rag.build_citations(chunks)
    usage = rag.get_last_usage() or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    retrieval_debug = rag.get_last_retrieval_debug() or {}
    history_builder_debug = rag.get_last_history_debug() or {}
    debug = {
        "kb_id": s.knowledge_base_id,
        "top_k": top_k,
        "index": idx_override,
        "index_mode": index_mode,
        "variant": variant,
        "retrieval": retrieval_debug,
        "history": {"builder": history_builder_debug, "stm": history_debug_dict},
        "memory": memory_debug_raw,
    }

    memory_service.record_memories(
        user_id=current_user.id,
        session_id=session_id,
        question=question,
        citations=citations,
    )
    if not memory_debug_raw.get("disabled"):
        memory_result = memory_service.record_guided_result(
            session_id=session_id,
            success=bool((retrieval_debug.get("memory") or {}).get("top_hit")),
        )
    else:
        memory_result = {"success": False, "auto_disabled": True}
    memory_debug_raw["result"] = memory_result

    # 事件日志（非阻塞）
    try:
        AskEventLogger().log_event({
            "user_id": str(current_user.id),
            "session_id": session_id,
            "kb_id": int(s.knowledge_base_id),
            "question": str(question)[:512],
            "top_k": int(top_k),
            "strategy": getattr(settings, "SM_RETRIEVAL_STRATEGY", "multi_stage"),
            "hits": len(chunks),
            "retrieval": retrieval_debug,
            "citations": citations,
            "usage": usage,
            "answer_chars": len(content or ""),
            "variant": variant,
            "historyUsage": {"builder": history_builder_debug, "stm": history_debug_dict, "compress": bool(compress_history)},
            "memory": {"request": memory_debug_raw, "result": memory_result},
            "index": idx_override,
            "index_mode": index_mode,
        })
    except Exception:
        pass

    # 持久化滚动摘要（若生成且开关开启）
    try:
        _summary = rag.get_last_history_summary()
        if _summary and settings.ENABLE_ROLLING_SUMMARY:
            SessionService(db).update_rolling_summary(session_id=session_id, rolling_summary=_summary)
    except Exception:
        pass
    # 持久化本轮问答
    try:
        db.add(
            Message(
                session_id=session_id,
                user_question=question,
                model_answer=content,
                retrieval_content=json.dumps({
                    "citations": citations,
                    "retrieval": retrieval_debug,
                    "memory": memory_debug_raw,
                }, ensure_ascii=False),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
    return JSONResponse(content={"answer": content, "chunks": chunks, "citations": citations, "usage": usage, "debug": debug})


@router.post("/{session_id}/compare", response_model=CompareResponse, summary="跨论文对比（生成 Markdown 表格 + citations）")
def compare_documents(
    session_id: str,
    payload: CompareRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """在同一会话下，针对指定 docIds 与维度执行聚焦检索，并生成结构化对比结果。"""
    svc = SessionService(db)
    s = svc.get_session_by_id(session_id=session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    if str(current_user.id) != str(s.user_id):
        raise HTTPException(status_code=403, detail="无权访问该会话")
    if not s.knowledge_base_id:
        raise HTTPException(status_code=400, detail="该会话未绑定知识库")

    # 读取会话默认 topK
    top_k = None
    if s.defaults_json:
        try:
            d = json.loads(s.defaults_json)
            if isinstance(d.get("topK"), int):
                top_k = d.get("topK")
        except Exception:
            pass
    top_k = top_k if isinstance(top_k, int) and 1 <= top_k <= 50 else settings.SM_RAG_TOPK

    rag = RAGService()

    # 校验 docIds 均属于当前 KB 且归属当前用户
    try:
        for _doc_id in (payload.docIds or []):
            _doc_svc.get_document_by_id(db=db, doc_id=int(_doc_id), user_id=int(current_user.id), kb_id=int(s.knowledge_base_id))
    except Exception as e:
        raise HTTPException(status_code=403, detail=f"无权访问文档或文档不属于该知识库: {e}")

    idx_override = f"sm_sess_{session_id}"
    try:
        result = rag.compare_documents(
            kb_id=int(s.knowledge_base_id),
            doc_ids=payload.docIds,
            dimensions=payload.dimensions,
            top_k=top_k,
            index_override=idx_override,
        )
        content = result.get("answer")
        chunks = result.get("chunks") or []
    except Exception:
        raise HTTPException(status_code=502, detail="Compare generation failed")

    citations = rag.build_citations(chunks)
    usage = rag.get_last_usage() or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    # for debug/log only
    dims = [str(x).strip() for x in (payload.dimensions or []) if str(x).strip()]
    dims_text = ", ".join(dims)
    question = (
        f"请对比以下维度：{dims_text}。以 Markdown 表格输出：列=论文（按标题或文档ID），行=维度；每格附必要引用。"
        if settings.SM_DEFAULT_LANGUAGE == "zh"
        else f"Compare the following dimensions: {dims_text}. Output a Markdown table with citations."
    )
    debug = {
        "kb_id": s.knowledge_base_id,
        "top_k": top_k,
        "index": idx_override,
        "docIds": payload.docIds,
        "dimensions": dims,
        "retrieval": rag.get_last_retrieval_debug() or {},
    }

    # 记录一条对比事件日志（与 ask 同结构，便于后续统一分析）
    try:
        AskEventLogger().log_event({
            "user_id": str(current_user.id),
            "session_id": session_id,
            "kb_id": int(s.knowledge_base_id),
            "question": question[:512],
            "top_k": int(top_k),
            "strategy": getattr(settings, "SM_RETRIEVAL_STRATEGY", "multi_stage"),
            "hits": len(chunks),
            "retrieval": rag.get_last_retrieval_debug() or {},
            "citations": citations,
            "usage": usage,
            "answer_chars": len(content or ""),
            "variant": "compare",
        })
    except Exception:
        pass

    return CompareResponse(answer=content or "", citations=citations, usage=usage, debug=debug)
