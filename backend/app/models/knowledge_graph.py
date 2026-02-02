from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    TIMESTAMP,
    Text,
    JSON,
    Float,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from models.base import Base


class KnowledgeGraphNode(Base):
    """Knowledge graph entity node."""

    __tablename__ = "knowledge_graph_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="节点ID")
    knowledge_base_id = Column(
        Integer,
        ForeignKey("knowledgebases.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联的知识库ID",
    )
    name = Column(String(255), nullable=False, comment="实体名称")
    normalized = Column(String(255), nullable=False, comment="实体归一化名称")
    entity_type = Column(String(64), nullable=True, comment="实体类型")
    aliases = Column(JSON, nullable=True, comment="实体别名列表")
    description = Column(Text, nullable=True, comment="实体描述")

    created_at = Column(TIMESTAMP, nullable=False, server_default=func.now(), comment="创建时间")
    updated_at = Column(
        TIMESTAMP,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="最后更新时间",
    )

    knowledge_base = relationship("KnowledgeBase")

    __table_args__ = (
        Index(
            "uq_kb_entity_norm",
            "knowledge_base_id",
            "normalized",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeGraphNode(id={self.id}, kb_id={self.knowledge_base_id}, name='{self.name}')>"


class KnowledgeGraphEdge(Base):
    """Knowledge graph relation edge."""

    __tablename__ = "knowledge_graph_edges"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="关系ID")
    knowledge_base_id = Column(
        Integer,
        ForeignKey("knowledgebases.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联的知识库ID",
    )
    source_node_id = Column(
        Integer,
        ForeignKey("knowledge_graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
        comment="源节点ID",
    )
    target_node_id = Column(
        Integer,
        ForeignKey("knowledge_graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
        comment="目标节点ID",
    )
    relation = Column(String(128), nullable=False, comment="关系类型")
    weight = Column(Float, nullable=True, comment="关系权重")

    created_at = Column(TIMESTAMP, nullable=False, server_default=func.now(), comment="创建时间")

    source_node = relationship("KnowledgeGraphNode", foreign_keys=[source_node_id])
    target_node = relationship("KnowledgeGraphNode", foreign_keys=[target_node_id])
    knowledge_base = relationship("KnowledgeBase")

    __table_args__ = (
        Index(
            "uq_kb_edge_relation",
            "knowledge_base_id",
            "source_node_id",
            "target_node_id",
            "relation",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeGraphEdge(id={self.id}, kb_id={self.knowledge_base_id}, "
            f"relation='{self.relation}')>"
        )


class KnowledgeGraphEvidence(Base):
    """Evidence links nodes/edges to documents and chunks."""

    __tablename__ = "knowledge_graph_evidence"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="证据ID")
    knowledge_base_id = Column(
        Integer,
        ForeignKey("knowledgebases.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联的知识库ID",
    )
    node_id = Column(
        Integer,
        ForeignKey("knowledge_graph_nodes.id", ondelete="CASCADE"),
        nullable=True,
        comment="关联节点ID",
    )
    edge_id = Column(
        Integer,
        ForeignKey("knowledge_graph_edges.id", ondelete="CASCADE"),
        nullable=True,
        comment="关联关系ID",
    )
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联文档ID",
    )
    chunk_id = Column(String(128), nullable=True, comment="关联chunk_id")
    score = Column(Float, nullable=True, comment="证据权重")
    evidence_text = Column(Text, nullable=True, comment="证据文本")
    metadata = Column(JSON, nullable=True, comment="证据元数据")

    created_at = Column(TIMESTAMP, nullable=False, server_default=func.now(), comment="创建时间")

    node = relationship("KnowledgeGraphNode", foreign_keys=[node_id])
    edge = relationship("KnowledgeGraphEdge", foreign_keys=[edge_id])
    knowledge_base = relationship("KnowledgeBase")

    __table_args__ = (
        Index(
            "idx_kb_node_edge",
            "knowledge_base_id",
            "node_id",
            "edge_id",
        ),
        Index(
            "idx_kb_doc_chunk",
            "knowledge_base_id",
            "document_id",
            "chunk_id",
        ),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeGraphEvidence(id={self.id}, kb_id={self.knowledge_base_id})>"
