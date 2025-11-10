import asyncio

from dataclasses import asdict
import json
import os
import uuid
from typing import Any, List as _List, Optional

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

    defaults = req.defaults.model_copy() if req.defaults is not None else SessionDefaults()

    if req.ephemeral:
        kb_name = f"temp_kb_for_{session_id}"
        kb = create_kb_for_user(
            db=db,
            kb_create=KnowledgeBaseCreate(name=kb_name, description=None, is_ephemeral=True),
            user_id=current_user.id,
        )
        kb_id = kb.id
        logger.info(f"Created ephemeral KB id={kb_id} for session {session_id}")
        if req.defaults is None:
            defaults.useSessionKnowledgeBase = True
            defaults.useUserKnowledgeBase = False
            defaults.userKnowledgeBaseId = None
    elif req.kbId:
        kb = get_kb_by_id(db=db, kb_id=req.kbId, user_id=current_user.id)
        kb_id = kb.id
        logger.info(f"Bind session {session_id} to existing KB id={kb_id}")
        if defaults.useUserKnowledgeBase and defaults.userKnowledgeBaseId is None:
            defaults.userKnowledgeBaseId = kb_id
        if req.defaults is None:
            defaults.useSessionKnowledgeBase = False
            defaults.useUserKnowledgeBase = True
            defaults.userKnowledgeBaseId = kb_id
    else:
        raise HTTPException(
            status_code=400,
            detail="必须提供 kbId 或将 ephemeral 设为 true。",
        )

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
    data = payload.model_dump()
    if data.get("useSessionKnowledgeBase"):
        if s.knowledge_base_id is None:
            raise HTTPException(status_code=400, detail="当前会话没有可用的临时知识库")
    if data.get("useUserKnowledgeBase"):
        user_kb_id = data.get("userKnowledgeBaseId")
        if user_kb_id is None:
            raise HTTPException(status_code=400, detail="启用本地知识库时必须选择知识库")
        get_kb_by_id(db=db, kb_id=user_kb_id, user_id=current_user.id)
    else:
        data["userKnowledgeBaseId"] = None

    normalized = SessionDefaults(**data)
    svc.update_defaults_json(
        session_id=session_id,
        defaults_json=json.dumps(normalized.model_dump(), ensure_ascii=False),
    )
    return normalized


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


@router.post("/{session_id}/upload-for-context", summary="上传文件并提取内容作为对话上下文")
def upload_for_context(
    session_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    轻量级文件上传：提取文件内容作为对话上下文，不触发RAG索引。
    适用于用户希望直接将文档内容作为对话上下文，而不是通过RAG检索的场景。
    """
    session_svc = SessionService(db)
    s = session_svc.get_session_by_id(session_id=session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    if str(current_user.id) != str(s.user_id):
        raise HTTPException(status_code=403, detail="无权操作该会话")

    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    allowed_exts = {".pdf", ".docx", ".txt"}
    if not any(file.filename.lower().endswith(ext) for ext in allowed_exts):
        raise HTTPException(status_code=400, detail=f"仅支持 {', '.join(allowed_exts)} 格式")

    # 保存临时文件
    import tempfile
    import os
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
            content = file.file.read()
            tmp.write(content)
            tmp_path = tmp.name

        # 使用轻量级解析器提取文本
        from service.core.ingestion.document_parser import LightweightDocumentParser
        parser = LightweightDocumentParser()
        blocks = parser.parse(file_path=tmp_path)
        
        # 合并所有文本块
        extracted_text = "\n\n".join([block.text for block in blocks if block.text.strip()])
        
        # 限制文本长度（支持现代大模型的长上下文）
        max_chars = 400000  # 约 100k tokens，支持 128k token 窗口的模型
        if len(extracted_text) > max_chars:
            extracted_text = extracted_text[:max_chars] + "\n\n[文档内容过长，已截断]"

        # 清理临时文件
        os.remove(tmp_path)

        # 将文件内容存储到会话的临时上下文中
        context_data = s.context_json or {}
        if isinstance(context_data, str):
            try:
                context_data = json.loads(context_data)
            except Exception:
                context_data = {}
        
        if "uploaded_files" not in context_data:
            context_data["uploaded_files"] = []
        
        context_data["uploaded_files"].append({
            "filename": file.filename,
            "content": extracted_text,
            "uploaded_at": __import__("datetime").datetime.utcnow().isoformat(),
        })
        
        s.context_json = json.dumps(context_data, ensure_ascii=False)
        db.add(s)
        db.commit()
        
        logger.info(f"[UPLOAD_FOR_CONTEXT] session={session_id} filename={file.filename} content_len={len(extracted_text)} total_files={len(context_data['uploaded_files'])}")

        return {"filename": file.filename, "content": extracted_text}

    except Exception as e:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)
        logger.error(f"文件内容提取失败: {e}")
        raise HTTPException(status_code=500, detail=f"文件内容提取失败: {str(e)}")


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

    result = svc.delete_session(session_id=session_id)
    return result


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

    top_k = top_k if isinstance(top_k, int) and 1 <= top_k <= 50 else settings.SM_RAG_TOPK
    temperature = temperature if isinstance(temperature, (int, float)) else settings.SM_TEMPERATURE
    max_tokens = max_tokens if isinstance(max_tokens, int) else settings.SM_MAX_TOKENS

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
        # 仅启用一个知识库，但被同时标记为会话/用户时，避免重复检索。
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

    rag = RAGService()
    stm_builder = ShortTermMemoryBuilder(db)
    memory_service = LongTermMemoryService(db)
    # 实验分流（稳定一致）
    variant = assign_variant(user_id=current_user.id, session_id=session_id, key="ask_mq_rrf", buckets=("A","B"))
    session_index_name = f"sm_sess_{session_id}"

    def merge_chunks(candidates, limit: int):
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
        all_candidates = []
        sources_debug: dict[str, Any] = {}
        latest_debug: dict[str, Any] = {}
        for label, kb_id_value in retrieval_plan:
            mode_override = "session_only" if label == "session" else "global_only"
            subset = rag.retrieve(
                query=question,
                kb_id=kb_id_value,
                top_k=top_k,
                focus_doc_ids=focus_ids,
                boost_doc_ids=boost_ids,
                session_index=session_index_name if label == "session" else None,
                index_mode=mode_override,
            )
            for chunk in subset:
                metadata = chunk.setdefault("metadata", {})
                metadata["knowledge_base_scope"] = label
                metadata["knowledge_base_id"] = kb_id_value
            all_candidates.extend(subset)
            debug_snapshot = rag.get_last_retrieval_debug() or {}
            sources_debug[label] = debug_snapshot
            latest_debug = debug_snapshot
        merged = merge_chunks(all_candidates, top_k)
        index_descriptor = " | ".join(f"{label}:{kb_id}" for label, kb_id in retrieval_plan) or "disabled"
        return merged, latest_debug, sources_debug, index_descriptor

    # 提取 context_json 中的文件内容（用于 RAG 关闭时的上下文）
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
                # 构建用于 LLM 的上下文文本
                file_texts = []
                for f in uploaded_files:
                    file_texts.append(f"--- 文件: {f.get('filename')} ---\n{f.get('content', '')}\n--- 文件结束 ---")
                context_text_for_llm = "\n\n".join(file_texts)
                logger.info(f"[CONTEXT_JSON_LOADED] session={session_id} files_count={len(uploaded_files)} context_text_len={len(context_text_for_llm)}")
        except Exception as ctx_err:
            logger.warning(f"Failed to parse context_json: {ctx_err}")

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
                if retrieval_sources:
                    progress_payload["retrieval_sources"] = retrieval_sources
                yield f"event: progress\ndata: {_json.dumps(progress_payload)}\n\n"

                hb = rag.get_last_history_debug() or {}
                history_usage = {
                    "builder": hb,
                    "stm": history_debug_dict,
                }

                answer_accum: list[str] = []
                # 如果有上下文文件且 RAG 关闭，将文件内容添加到 chunks
                effective_chunks = chunks0
                extra_system_prompt = None
                if context_text_for_llm and index_mode == "disabled":
                    # 将文件内容作为一个特殊的 chunk 传递给 LLM
                    context_chunk = {
                        "chunk_id": "context_file",
                        "text": context_text_for_llm,
                        "metadata": {
                            "document_id": "用户上传文档",
                            "page": 1,
                            "source": "uploaded_context",
                            "type": "file_content"
                        },
                    }
                    effective_chunks = [context_chunk] + chunks0
                    # 添加额外的系统提示，告诉 LLM 这是用户上传的文档
                    extra_system_prompt = "用户已上传文档作为对话上下文，请基于文档内容回答问题。" if rag.prompt.language == "zh" else "The user has uploaded documents as conversation context. Please answer based on the document content."
                    logger.info(f"[CONTEXT_FILE_ADDED] session={session_id} context_text_len={len(context_text_for_llm)} chunks_count={len(effective_chunks)} extra_prompt={extra_system_prompt}")
                
                for part in rag.generate(question=question, chunks=effective_chunks, stream=True, history=history_list, compress_history=compress_history, rolling_summary=s.rolling_summary, extra_system=extra_system_prompt):
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
                    logger.info(f"[STREAM_BEFORE_SAVE] session={session_id} answer_parts={len(answer_accum)} full_answer_len={len(full_answer)} question_len={len(question)}")
                    memory_service.record_memories(
                        user_id=current_user.id,
                        session_id=session_id,
                        question=question,
                        citations=citations_tail,
                    )
                    retrieval_data = {
                        "citations": citations_tail,
                        "retrieval": retrieval_debug,
                        "memory": memory_debug_raw,
                    }
                    if retrieval_sources:
                        retrieval_data["retrieval_sources"] = retrieval_sources
                    if context_files:
                        retrieval_data["context_files"] = context_files
                    
                    msg = Message(
                        session_id=session_id,
                        user_question=question,
                        model_answer=full_answer,
                        retrieval_content=_json.dumps(retrieval_data, ensure_ascii=False),
                    )
                    db.add(msg)
                    db.commit()
                    logger.info(f"[STREAM_SAVE_OK] session={session_id} question_len={len(question)} answer_len={len(full_answer)} msg_id={msg.message_id}")
                    
                    # 清空 context_json 中的文件内容（避免影响后续消息）
                    if context_files:
                        try:
                            s.context_json = None
                            db.add(s)
                            db.commit()
                            logger.info(f"[CONTEXT_CLEARED] session={session_id}")
                        except Exception as clear_err:
                            logger.warning(f"Failed to clear context_json: {clear_err}")
                except Exception as save_err:
                    logger.error(f"[STREAM_SAVE_FAIL] session={session_id} error={save_err}")
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
    chunks, retrieval_debug, retrieval_sources, idx_override = perform_retrieval(
        question=question,
        top_k=top_k,
        focus_ids=focus_ids,
        boost_ids=boost_doc_ids,
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
        # 如果有上下文文件且 RAG 关闭，将文件内容添加到 chunks
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
                    "type": "file_content"
                },
            }
            effective_chunks_non_stream = [context_chunk] + chunks
            extra_system_prompt_non_stream = "用户已上传文档作为对话上下文，请基于文档内容回答问题。" if rag.prompt.language == "zh" else "The user has uploaded documents as conversation context. Please answer based on the document content."
        
        content = rag.generate(question=question, chunks=effective_chunks_non_stream, temperature=temperature, max_tokens=max_tokens, stream=False, history=history_list, compress_history=compress_history, rolling_summary=s.rolling_summary, extra_system=extra_system_prompt_non_stream)
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
        "kb_id": primary_kb_for_debug,
        "kb_ids": kb_ids_for_debug,
        "top_k": top_k,
        "index": idx_override,
        "index_mode": index_mode,
        "variant": variant,
        "retrieval": retrieval_debug,
        "history": {"builder": history_builder_debug, "stm": history_debug_dict},
        "memory": memory_debug_raw,
    }
    if retrieval_sources:
        debug["retrieval_sources"] = retrieval_sources

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
            "kb_id": int(primary_kb_for_debug) if primary_kb_for_debug is not None else None,
            "kb_ids": kb_ids_for_debug,
            "question": str(question)[:512],
            "top_k": int(top_k),
            "strategy": getattr(settings, "SM_RETRIEVAL_STRATEGY", "multi_stage"),
            "hits": len(chunks),
            "retrieval": retrieval_debug,
            "retrieval_sources": retrieval_sources,
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
        retrieval_data_non_stream = {
            "citations": citations,
            "retrieval": retrieval_debug,
            "memory": memory_debug_raw,
        }
        if retrieval_sources:
            retrieval_data_non_stream["retrieval_sources"] = retrieval_sources
        if context_files:
            retrieval_data_non_stream["context_files"] = context_files
        
        db.add(
            Message(
                session_id=session_id,
                user_question=question,
                model_answer=content,
                retrieval_content=json.dumps(retrieval_data_non_stream, ensure_ascii=False),
            )
        )
        db.commit()
        
        # 清空 context_json 中的文件内容（避免影响后续消息）
        if context_files:
            try:
                s.context_json = None
                db.add(s)
                db.commit()
                logger.info(f"[CONTEXT_CLEARED] session={session_id}")
            except Exception as clear_err:
                logger.warning(f"Failed to clear context_json: {clear_err}")
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
