from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Body, BackgroundTasks
from sqlalchemy.orm import Session
from models.user import User
from schemas.document import DocumentInDB, DocumentUpdate, DocumentCreate
from schemas.knowledge_base import KnowledgeBaseInDB, KnowledgeBaseCreate, KnowledgeBaseUpdate
from service.auth import get_current_user
from service import document_service
from service.ingestion_service import ingestion_service
from service.job_service import job_service
from models.job import JobType, JobStatus
from schemas.job import JobInDB
from utils.database import get_db
from exceptions.base import ResourceNotFoundException, PermissionDeniedException, APIException
from pydantic import BaseModel
from typing import List as _List
from fastapi import UploadFile, File
from service.core.api.utils.file_storage import FileStorageUtil
from service.job_runner_service import execute_job
from service.job_handler.online_ingestion_handler import OnlineIngestionHandler
from service.job_handler.local_upload_handler import LocalUploadHandler
from service import knowledgebase_service


router = APIRouter()

@router.post("/", response_model=KnowledgeBaseInDB, status_code=status.HTTP_201_CREATED)
def create_knowledge_base(
    *,
    db: Session = Depends(get_db),
    kb_in: KnowledgeBaseCreate,
    current_user: User = Depends(get_current_user)
):
    """
    为当前认证的用户创建一个新的知识库。
    """
    kb = knowledgebase_service.create_kb_for_user(db=db, kb_create=kb_in, user_id=current_user.id)
    return kb

@router.get("/", response_model=List[KnowledgeBaseInDB])
def list_knowledge_bases(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取当前认证用户的所有知识库列表。
    """
    return knowledgebase_service.list_kbs_by_user_id(db=db, user_id=current_user.id)

@router.get("/{kb_id}", response_model=KnowledgeBaseInDB)
def get_knowledge_base(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取指定ID的知识库详情。
    """
    try:
        return knowledgebase_service.get_kb_by_id(db=db, kb_id=kb_id, user_id=current_user.id)
    except (ResourceNotFoundException, PermissionDeniedException) as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

@router.patch("/{kb_id}", response_model=KnowledgeBaseInDB)
def update_knowledge_base(
    kb_id: int,
    kb_in: KnowledgeBaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    更新指定ID的知识库。
    """
    try:
        return knowledgebase_service.update_kb(db=db, kb_id=kb_id, kb_update=kb_in, user_id=current_user.id)
    except (ResourceNotFoundException, PermissionDeniedException) as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

@router.delete("/{kb_id}", response_model=KnowledgeBaseInDB)
def delete_knowledge_base(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    删除指定ID的知识库。
    """
    try:
        return knowledgebase_service.delete_kb(db=db, kb_id=kb_id, user_id=current_user.id)
    except (ResourceNotFoundException, PermissionDeniedException) as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


class CleanupRequest(BaseModel):
    olderThanHours: int = 24

@router.post(
    "/cleanup-ephemeral",
    summary="清理过期的临时知识库",
    description="按时间阈值（小时）清理当前用户的临时知识库及其文档/向量等资源。",
)
def cleanup_ephemeral_kbs(
    payload: CleanupRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.olderThanHours <= 0 or payload.olderThanHours > 24 * 365:
        raise HTTPException(status_code=400, detail="olderThanHours 范围不合法")
    cleaned = knowledgebase_service.cleanup_ephemeral_kbs(db, current_user.id, payload.olderThanHours)
    return {"cleaned": cleaned}

