from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

class DocumentBase(BaseModel):
    """
    文档的基础 Pydantic 模型，包含所有文档共有的核心元数据字段。
    """
    title: str
    authors: Optional[List[str]] = None
    abstract: Optional[str] = None
    publication_year: Optional[int] = None
    journal_or_conference: Optional[str] = None
    keywords: Optional[List[str]] = None
    citation_count: Optional[int] = None
    fields_of_study: Optional[List[str]] = None
    doi: Optional[str] = None
    semantic_scholar_id: Optional[str] = None
    source_url: Optional[str] = None
    local_pdf_path: Optional[str] = None

class DocumentCreate(DocumentBase):
    """
    用于在特定知识库中创建新文档的 Pydantic 模型。
    它只定义了API请求体中应该包含的字段。
    knowledge_base_id 将从URL路径参数中获取，而不是在请求体中。
    """
    pass

class DocumentUpdate(BaseModel):
    """
    用于更新文档的 Pydantic 模型。
    所有字段都设为可选，以便可以只更新部分字段。
    """
    title: Optional[str] = None
    authors: Optional[List[str]] = None
    abstract: Optional[str] = None
    publication_year: Optional[int] = None
    journal_or_conference: Optional[str] = None
    keywords: Optional[List[str]] = None
    citation_count: Optional[int] = None
    fields_of_study: Optional[List[str]] = None
    doi: Optional[str] = None
    semantic_scholar_id: Optional[str] = None
    source_url: Optional[str] = None
    local_pdf_path: Optional[str] = None

class DocumentInDB(DocumentBase):
    """
    作为API响应返回给客户端的文档模型。
    它继承自 DocumentBase，并增加了数据库自动生成的字段。
    """
    id: int
    knowledge_base_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
