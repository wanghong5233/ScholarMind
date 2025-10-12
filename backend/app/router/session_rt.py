from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Query, Body
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session
from schemas.session import CreateSessionRequest, CreateSessionResponse, SessionDefaults, SessionDetail
from schemas.knowledge_base import KnowledgeBaseCreate
from service.knowledgebase_service import create_kb_for_user, get_kb_by_id
from service.session_service import SessionService
from service.job_service import job_service
from service.job_runner_service import execute_job
from service.job_handler.local_upload_handler import LocalUploadHandler
from service.core.api.utils.file_storage import FileStorageUtil
from service.core.rag.retrieval.vector_store import ESVectoreStore, RetrieveQuery
from service.core.rag.service import RAGService
from schemas.rag import Chunk as RagChunk
from utils.database import get_db
from utils.get_logger import logger
from utils.rate_limiter import rate_limiter
from utils.quota import quota
from models.user import User
from service.auth import get_current_user
import uuid
import json
from typing import List as _List, Optional
import os
from core.config import settings

router = APIRouter()


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

    idx_override = f"sm_sess_{session_id}" if use_session_index else None
    focus_ids_list = None
    if focus_doc_ids:
        try:
            focus_ids_list = [int(x) for x in focus_doc_ids.split(",") if x.strip().isdigit()]
        except Exception:
            focus_ids_list = None

    store = ESVectoreStore(default_index=settings.ES_DEFAULT_INDEX)
    rq = RetrieveQuery(text=q, kb_id=int(s.knowledge_base_id), top_k=top_k, focus_doc_ids=focus_ids_list, index_override=idx_override)
    results = store.search(query=rq)

    out: list[RagChunk] = []
    for r in results:
        md = r.metadata or {}
        out.append(
            RagChunk(
                chunk_id=r.chunk_id,
                document_id=str(md.get("document_id", "")),
                content=r.text,
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
    payload: dict = Body(..., description="{ question: string, stream?: boolean, focusDocIds?: number[], topK?: number, temperature?: number, maxTokens?: number }"),
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
    focus_ids = payload.get("focusDocIds") if isinstance(payload.get("focusDocIds"), list) else None

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

    if stream:
        def gen():
            try:
                idx_override = f"sm_sess_{session_id}"
                # 先检索，立即告知客户端检索完成，减少“无响应”体感
                chunks0 = rag.retrieve(
                    query=question,
                    kb_id=int(s.knowledge_base_id),
                    top_k=top_k,
                    focus_doc_ids=focus_ids,
                    index_override=idx_override,
                )
                import json as _json
                yield f"event: progress\ndata: {_json.dumps({'stage':'retrieved','hits':len(chunks0),'index':idx_override})}\n\n"

                for part in rag.generate(question=question, chunks=chunks0, stream=True):
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
                debug_tail = {"kb_id": s.knowledge_base_id, "top_k": top_k, "index": idx_override}
                import json as _json
                tail = _json.dumps({"citations": citations_tail, "usage": usage_tail, "debug": debug_tail}, ensure_ascii=False)
                yield f"event: completion\ndata: {tail}\n\n"
            except Exception as e:
                try:
                    logger.error(f"ASK stream error user={current_user.id} session={session_id}: {e}")
                except Exception:
                    pass
                yield f"event: error\ndata: [Stream Error]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8")

    # non-streaming
    idx_override = f"sm_sess_{session_id}"
    chunks = rag.retrieve(
        query=question,
        kb_id=int(s.knowledge_base_id),
        top_k=top_k,
        focus_doc_ids=focus_ids,
        index_override=idx_override,
    )
    try:
        content = rag.generate(question=question, chunks=chunks, temperature=temperature, max_tokens=max_tokens, stream=False)
    except Exception as e:
        try:
            logger.error(f"ASK generate error user={current_user.id} session={session_id}: {e}")
        except Exception:
            pass
        raise HTTPException(status_code=502, detail="LLM generation failed")
    citations = rag.build_citations(chunks)
    usage = rag.get_last_usage() or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    debug = {"kb_id": s.knowledge_base_id, "top_k": top_k, "index": idx_override}
    return JSONResponse(content={"answer": content, "chunks": chunks, "citations": citations, "usage": usage, "debug": debug})
