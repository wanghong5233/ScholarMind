from __future__ import annotations

from sqlalchemy import Column, String, Text, TIMESTAMP, Float, Integer, JSON, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from models.base import Base


class Memory(Base):
    """长期记忆表，用于保存 Episodic / Semantic 记忆。"""

    __tablename__ = "memories"

    memory_id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id = Column(String(255), nullable=False, index=True)
    session_id = Column(String(16), nullable=True, index=True)
    memory_type = Column(String(32), nullable=False, index=True)  # episodic / semantic
    content = Column(Text, nullable=False)
    document_id = Column(String(64), nullable=True, index=True)
    meta_data = Column(JSON, nullable=True)  # 避免与 SQLAlchemy 保留字段 metadata 冲突
    importance = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    status = Column(String(16), nullable=False, default="active")
    embedding = Column(JSON, nullable=True)
    summary = Column(Text, nullable=True)
    access_count = Column(Integer, nullable=False, default=0)
    last_accessed = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP, nullable=False, server_default=func.now(), onupdate=func.now())


