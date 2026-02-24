from typing import List as _List, Optional

from fastapi import APIRouter, Depends, BackgroundTasks, UploadFile, File, Query, Body, HTTPException
from sqlalchemy.orm import Session

from core.config import settings
from models.user import User
from schemas.rag import Chunk as RagChunk
from schemas.session import (
    AskRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    SessionDefaults,
    SessionDetail,
    SessionRenameRequest,
    SessionRewindRequest,
    CompareRequest,
    CompareResponse,
)
from service.auth import get_current_user
from service.core.conversation.chat_ask_orchestrator import ChatAskOrchestrator
from service.core.conversation.chat_compare_orchestrator import ChatCompareOrchestrator
from service.core.conversation.session_management_service import SessionManagementService
from service.core.conversation.session_retrieval_service import SessionRetrievalService
from service.core.conversation.session_upload_service import SessionUploadService
from utils.database import get_db

router = APIRouter()


@router.get("/{session_id}/messages", summary="分页获取会话完整历史")
def list_messages(
    session_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = SessionManagementService(db=db, current_user=current_user)
    return service.list_messages(session_id=session_id, page=page, page_size=page_size)


@router.post("/", response_model=CreateSessionResponse)
def create_session(
    req: CreateSessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    创建新会话（每个 session 必绑定专属 Session KB）。

    - 会话创建时始终生成 Session KB；
    - 可选提供 kbId 作为默认关联知识库（用户知识库）；
    - Ask/Agent 共用同一会话记忆与会话知识库。
    """
    service = SessionManagementService(db=db, current_user=current_user)
    return service.create_session(req=req)


@router.post("/{session_id}/create-and-upload", summary="一步创建会话并上传（可选复用已有会话）")
def create_and_upload(
    session_id: Optional[str] = None,
    files: _List[UploadFile] = File(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = None,
):
    """若未传 session_id 则创建新会话并绑定 Session KB，然后上传。
    若传入 session_id 则复用其 Session KB 直接上传。"""
    service = SessionUploadService(db=db, current_user=current_user)
    return service.create_and_upload(
        session_id=session_id,
        files=files,
        file_single=file,
        background_tasks=background_tasks,
    )


@router.get("/{session_id}", response_model=SessionDetail)
def get_session_detail(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = SessionManagementService(db=db, current_user=current_user)
    return service.get_session_detail(session_id=session_id)


@router.put("/{session_id}/name", response_model=SessionDetail, summary="重命名会话")
def rename_session(
    session_id: str,
    payload: SessionRenameRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = SessionManagementService(db=db, current_user=current_user)
    return service.rename_session(session_id=session_id, session_name=payload.session_name)


@router.get("/{session_id}/defaults", response_model=SessionDefaults)
def get_session_defaults(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = SessionManagementService(db=db, current_user=current_user)
    return service.get_session_defaults(session_id=session_id)


@router.put("/{session_id}/defaults", response_model=SessionDefaults)
def update_session_defaults(
    session_id: str,
    payload: SessionDefaults,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = SessionManagementService(db=db, current_user=current_user)
    return service.update_session_defaults(session_id=session_id, payload=payload)


@router.post("/{session_id}/upload", summary="基于会话的本地上传（异步）")
def upload_by_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    files: _List[UploadFile] = File(None),
    file_single: UploadFile | None = File(None, alias="file"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = SessionUploadService(db=db, current_user=current_user)
    return service.upload_by_session(
        session_id=session_id,
        background_tasks=background_tasks,
        files=files,
        file_single=file_single,
    )


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
    service = SessionUploadService(db=db, current_user=current_user)
    return service.upload_for_context(session_id=session_id, file=file)


@router.get("/{session_id}/retrieve", response_model=list[RagChunk], summary="最小检索验证")
def retrieve_by_session(
    session_id: str,
    q: str = Query(..., description="查询文本"),
top_k: int = Query(settings.SM_RAG_TOPK, ge=1, le=50),
    focus_doc_ids: Optional[str] = Query(None, description="以逗号分隔的 document_id 列表"),
    use_session_index: bool = Query(False, description="是否使用会话级临时索引"),
    index_mode: Optional[str] = Query(None, description="索引检索模式: auto/session_only/global_only/hybrid"),
    provider: Optional[str] = Query(None, description="RAG provider override"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = SessionRetrievalService(db=db, current_user=current_user)
    return service.retrieve(
        session_id=session_id,
        q=q,
        top_k=top_k,
        focus_doc_ids=focus_doc_ids,
        use_session_index=use_session_index,
        index_mode=index_mode,
        provider=provider,
    )


@router.delete("/{session_id}", summary="删除会话并清理临时资源")
def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = SessionManagementService(db=db, current_user=current_user)
    return service.delete_session(session_id=session_id)


@router.post("/{session_id}/rewind", summary="回卷会话历史（删除指定节点及之后消息）")
def rewind_session_messages(
    session_id: str,
    payload: SessionRewindRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = SessionManagementService(db=db, current_user=current_user)
    if payload.before_message_id:
        return service.rewind_messages(
            session_id=session_id,
            before_message_id=payload.before_message_id,
        )
    if payload.keep_messages is None:
        raise HTTPException(status_code=400, detail="keep_messages or before_message_id is required")
    return service.rewind_messages(
        session_id=session_id,
        keep_messages=payload.keep_messages,
    )


@router.post("/{session_id}/ask", summary="RAG 基础问答（流式/非流式）")
def ask(
    session_id: str,
    payload: AskRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    orchestrator = ChatAskOrchestrator(db=db, current_user=current_user)
    return orchestrator.handle(
        session_id=session_id,
        payload=payload.model_dump(exclude_none=True),
    )


@router.get("/{session_id}/ask/replay/{run_id}", summary="按 run_id+seq 回放问答流事件")
def replay_ask_stream(
    session_id: str,
    run_id: str,
    since_seq: int = Query(-1, ge=-1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    orchestrator = ChatAskOrchestrator(db=db, current_user=current_user)
    return orchestrator.replay_stream(
        session_id=session_id,
        run_id=run_id,
        since_seq=since_seq,
    )


@router.post("/{session_id}/ask/cancel/{run_id}", summary="取消指定问答流任务")
def cancel_ask_stream(
    session_id: str,
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    orchestrator = ChatAskOrchestrator(db=db, current_user=current_user)
    return orchestrator.cancel_run(
        session_id=session_id,
        run_id=run_id,
    )


@router.post("/{session_id}/compare", response_model=CompareResponse, summary="跨论文对比（生成 Markdown 表格 + citations）")
def compare_documents(
    session_id: str,
    payload: CompareRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """在同一会话下，针对指定 docIds 与维度执行聚焦检索，并生成结构化对比结果。"""
    orchestrator = ChatCompareOrchestrator(db=db, current_user=current_user)
    return orchestrator.handle(session_id=session_id, payload=payload)
