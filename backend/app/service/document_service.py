from typing import List, Optional, Set
from sqlalchemy.orm import Session
from sqlalchemy import or_
from models.document import Document
from models.knowledgebase import KnowledgeBase
from schemas.document import DocumentUpdate, DocumentCreate
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
    
    # 先删除本地文件（如果存在），失败容错，不阻塞整体删除
    try:
        import os
        if doc_to_delete.local_pdf_path and os.path.exists(doc_to_delete.local_pdf_path):
            os.remove(doc_to_delete.local_pdf_path)
    except Exception:
        pass

    # 同步删除向量库分块（尽力而为）
    try:
        # 依据现有ES schema，假设索引名为用户维度或统一索引，最稳妥是调用封装的连接器
        from service.core.rag.utils.es_conn import ESConnection
        es = ESConnection()
        # 根据字段规范，条件匹配 docnm 或 document_id，这里采用通用的 document_id = doc_id
        # 若项目实际字段不同，可扩展：按 kb_id + doc_id/文件名组合删除
        _ = es.delete({"document_id": doc_to_delete.id}, indexName=str(user_id), knowledgebaseId=str(kb_id))
    except Exception:
        pass

    db.delete(doc_to_delete)
    db.commit()
    
    return doc_to_delete


def _ensure_kb_access(db: Session, kb_id: int, user_id: int) -> KnowledgeBase:
    """
    校验知识库存在且属于当前用户，返回 KB。
    """
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise ResourceNotFoundException(f"KnowledgeBase with id {kb_id} not found.")
    if kb.user_id != user_id:
        raise PermissionDeniedException("You do not have permission to access this knowledge base.")
    return kb


def _find_duplicate_document(
    db: Session,
    kb_id: int,
    semantic_scholar_id: Optional[str],
    doi: Optional[str],
    file_hash: Optional[str]
) -> Optional[Document]:
    """
    在同一知识库内基于 semantic_scholar_id / doi / file_hash 查重。
    只要任意一个非空字段匹配，即视为重复。
    """
    query = db.query(Document).filter(Document.knowledge_base_id == kb_id)

    # 按优先级尝试匹配
    if semantic_scholar_id:
        existing = query.filter(Document.semantic_scholar_id == semantic_scholar_id).first()
        if existing:
            return existing
    if doi:
        existing = query.filter(Document.doi == doi).first()
        if existing:
            return existing
    if file_hash:
        existing = query.filter(Document.file_hash == file_hash).first()
        if existing:
            return existing
    return None


def find_document_by_file_hash(db: Session, kb_id: int, file_hash: str) -> Optional[Document]:
    """
    在指定知识库中通过文件哈希查找文档。
    """
    if not file_hash:
        return None
    return (
        db.query(Document)
        .filter(
            Document.knowledge_base_id == kb_id,
            Document.file_hash == file_hash,
        )
        .first()
    )


def create_documents_bulk_for_kb(
    db: Session,
    kb_id: int,
    user_id: int,
    documents: List[DocumentCreate]
) -> List[Document]:
    """
    批量创建文档（带去重），并返回成功创建的文档列表。

    - 先校验用户对知识库的访问权限
    - 对每个文档基于 semantic_scholar_id / doi / file_hash 在 KB 内查重
    - 非重复则创建，重复则跳过
    """
    _ensure_kb_access(db, kb_id, user_id)

    created: List[Document] = []

    for doc in documents:
        duplicate = _find_duplicate_document(
            db=db,
            kb_id=kb_id,
            semantic_scholar_id=doc.semantic_scholar_id,
            doi=doc.doi,
            file_hash=doc.file_hash,
        )
        if duplicate:
            # 跳过重复
            continue

        new_doc = Document(
            knowledge_base_id=kb_id,
            title=doc.title,
            authors=doc.authors,
            abstract=doc.abstract,
            publication_year=doc.publication_year,
            journal_or_conference=doc.journal_or_conference,
            keywords=doc.keywords,
            citation_count=doc.citation_count,
            fields_of_study=doc.fields_of_study,
            doi=doc.doi,
            semantic_scholar_id=doc.semantic_scholar_id,
            source_url=doc.source_url,
            local_pdf_path=doc.local_pdf_path,
            file_hash=doc.file_hash,
            ingestion_source=doc.ingestion_source.value if hasattr(doc.ingestion_source, "value") else doc.ingestion_source,
        )

        db.add(new_doc)
        db.flush()  # 先拿到自增ID
        created.append(new_doc)

    if created:
        db.commit()
        for d in created:
            db.refresh(d)

    return created


def find_existing_documents_for_payload(
    db: Session,
    kb_id: int,
    documents: List[DocumentCreate]
) -> List[Document]:
    """
    根据传入的 DocumentCreate 列表，在指定 KB 内查找已存在的文档。
    仅返回那些 semantic_scholar_id 或 DOI 匹配的记录。
    """
    semantic_ids: Set[str] = set(
        d.semantic_scholar_id for d in documents if getattr(d, "semantic_scholar_id", None)
    )
    dois: Set[str] = set(
        d.doi for d in documents if getattr(d, "doi", None)
    )

    if not semantic_ids and not dois:
        return []

    q = db.query(Document).filter(Document.knowledge_base_id == kb_id)
    conditions = []
    if semantic_ids:
        conditions.append(Document.semantic_scholar_id.in_(list(semantic_ids)))
    if dois:
        conditions.append(Document.doi.in_(list(dois)))

    return q.filter(or_(*conditions)).all()