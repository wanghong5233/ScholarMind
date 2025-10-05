from typing import List, Optional
from sqlalchemy.orm import Session
from models.knowledgebase import KnowledgeBase
from models.user import User
from schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseUpdate
from exceptions.base import ResourceNotFoundException, PermissionDeniedException

def create_kb_for_user(db: Session, kb_create: KnowledgeBaseCreate, user_id: int) -> KnowledgeBase:
    """
    为指定用户创建一个新的知识库。

    Args:
        db (Session): 数据库会话。
        kb_create (KnowledgeBaseCreate): 包含新知识库信息的Pydantic模型。
        user_id (int): 所属用户的ID。

    Returns:
        KnowledgeBase: 新创建的知识库ORM对象。
    """
    db_kb = KnowledgeBase(**kb_create.model_dump(), user_id=user_id)
    db.add(db_kb)
    db.commit()
    db.refresh(db_kb)
    return db_kb

def get_kb_by_id(db: Session, kb_id: int, user_id: int) -> Optional[KnowledgeBase]:
    """
    根据ID获取单个知识库，并校验所有权。

    Args:
        db (Session): 数据库会话。
        kb_id (int): 知识库的ID。
        user_id (int): 当前请求用户的ID。

    Returns:
        Optional[KnowledgeBase]: 找到的知识库ORM对象。

    Raises:
        ResourceNotFoundException: 如果知识库不存在。
        PermissionDeniedException: 如果用户不是该知识库的所有者。
    """
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise ResourceNotFoundException(f"KnowledgeBase with id {kb_id} not found.")
    if kb.user_id != user_id:
        raise PermissionDeniedException("You do not have permission to access this knowledge base.")
    return kb

def list_kbs_by_user_id(db: Session, user_id: int) -> List[KnowledgeBase]:
    """
    获取指定用户的所有知识库列表。

    Args:
        db (Session): 数据库会话。
        user_id (int): 用户的ID。

    Returns:
        List[KnowledgeBase]: 该用户的知识库ORM对象列表。
    """
    return db.query(KnowledgeBase).filter(KnowledgeBase.user_id == user_id).all()

def update_kb(db: Session, kb_id: int, kb_update: KnowledgeBaseUpdate, user_id: int) -> Optional[KnowledgeBase]:
    """
    更新一个知识库的名称或描述，并校验所有权。

    Args:
        db (Session): 数据库会话。
        kb_id (int): 要更新的知识库ID。
        kb_update (KnowledgeBaseUpdate): 包含更新信息的Pydantic模型。
        user_id (int): 当前请求用户的ID。

    Returns:
        Optional[KnowledgeBase]: 更新后的知识库ORM对象。
    """
    kb = get_kb_by_id(db, kb_id, user_id)  # 复用带有权限检查的查询函数
    
    update_data = kb_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(kb, key, value)
    
    db.commit()
    db.refresh(kb)
    return kb

def delete_kb(db: Session, kb_id: int, user_id: int) -> KnowledgeBase:
    """
    删除一个知识库，并校验所有权。
    由于我们在模型中设置了级联删除，相关的文档也会被自动删除。

    Args:
        db (Session): 数据库会话。
        kb_id (int): 要删除的知识库ID。
        user_id (int): 当前请求用户的ID。

    Returns:
        KnowledgeBase: 被删除的知识库对象。
    """
    kb = get_kb_by_id(db, kb_id, user_id) # 复用带有权限检查的查询函数

    db.delete(kb)
    db.commit()
    return kb
