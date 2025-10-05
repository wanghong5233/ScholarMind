from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseUpdate, KnowledgeBaseInDB
from service import knowledgebase_service
from utils.database import get_db
from models.user import User
from service.auth import get_current_user
from exceptions.base import ResourceNotFoundException, PermissionDeniedException

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
