from typing import List
from sqlalchemy.orm import Session
from models.document import Document
from models.knowledgebase import KnowledgeBase
from schemas.document import DocumentUpdate
from exceptions.base import ResourceNotFoundException, PermissionDeniedException

def get_document_by_id(db: Session, doc_id: int, user_id: int, kb_id: int = None) -> Document:
    """
    通过ID获取文档，并校验用户权限。

    Args:
        db (Session): 数据库会话。
        doc_id (int): 文档ID。
        user_id (int): 当前用户ID。
        kb_id (int, optional): 文档所属的知识库ID，用于校验。

    Returns:
        Document: 找到的文档模型实例。
    
    Raises:
        ResourceNotFoundException: 如果文档未找到。
        PermissionDeniedException: 如果用户无权访问该文档。
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise ResourceNotFoundException(f"Document with id {doc_id} not found.")

    # 增加校验：确保文档属于指定的知识库
    if kb_id is not None and doc.knowledge_base_id != kb_id:
        raise PermissionDeniedException("Document does not belong to the specified knowledge base.")
    
    # 校验用户是否有权访问该文档所属的知识库
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == doc.knowledge_base_id).first()
    if not kb or kb.user_id != user_id:
        raise PermissionDeniedException("You do not have permission to access this document.")
        
    return doc

def list_documents_by_kb_id(db: Session, kb_id: int, user_id: int) -> List[Document]:
    """
    获取指定知识库下的所有文档，并校验用户权限。

    Args:
        db (Session): 数据库会话。
        kb_id (int): 知识库ID。
        user_id (int): 当前用户ID。

    Returns:
        List[Document]: 文档模型实例列表。

    Raises:
        ResourceNotFoundException: 如果知识库未找到。
        PermissionDeniedException: 如果用户无权访问该知识库。
    """

    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise ResourceNotFoundException(f"KnowledgeBase with id {kb_id} not found.")
    if kb.user_id != user_id:
        raise PermissionDeniedException("You do not have permission to access this knowledge base.")
        
    return db.query(Document).filter(Document.knowledge_base_id == kb_id).all()

def update_document(db: Session, doc_id: int, doc_update: DocumentUpdate, user_id: int, kb_id: int) -> Document:
    """
    更新文档元数据。

    Args:
        db (Session): 数据库会话。
        doc_id (int): 要更新的文档ID。
        doc_update (DocumentUpdate): 包含更新数据的Pydantic模型。
        user_id (int): 当前用户ID。
        kb_id (int): 文档所属的知识库ID，用于校验。

    Returns:
        Document: 更新后的文档模型实例。
    """
    doc_to_update = get_document_by_id(db, doc_id, user_id, kb_id=kb_id)
    
    update_data = doc_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(doc_to_update, key, value)
        
    db.commit()
    db.refresh(doc_to_update)
    return doc_to_update

def delete_document(db: Session, doc_id: int, user_id: int, kb_id: int) -> Document:
    """
    从知识库中删除一个文档。
    
    注意：此函数目前只处理数据库层面的删除。
    后续需要扩展，以同步删除向量存储和文件存储中的数据。

    Args:
        db (Session): 数据库会话。
        doc_id (int): 要删除的文档ID。
        user_id (int): 当前用户ID。
        kb_id (int): 文档所属的知识库ID，用于校验。

    Returns:
        Document: 被删除的文档模型实例。
    """
    doc_to_delete = get_document_by_id(db, doc_id, user_id, kb_id=kb_id)
    
    db.delete(doc_to_delete)
    db.commit()
    
    return doc_to_delete