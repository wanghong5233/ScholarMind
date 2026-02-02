"""add rag provider fields and knowledge graph tables

Revision ID: 14_add_rag_provider_graph
Revises: 13_add_structure_metadata
Create Date: 2026-01-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "14_add_rag_provider_graph"
down_revision: Union[str, None] = "13_add_structure_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_cols = {col["name"] for col in inspector.get_columns("knowledgebases")}
    if "rag_provider" not in existing_cols:
        op.add_column(
            "knowledgebases",
            sa.Column("rag_provider", sa.String(length=64), nullable=True),
        )
    if "rag_config" not in existing_cols:
        op.add_column(
            "knowledgebases",
            sa.Column("rag_config", sa.JSON(), nullable=True),
        )

    tables = inspector.get_table_names()
    if "knowledge_graph_nodes" not in tables:
        op.create_table(
            "knowledge_graph_nodes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("knowledge_base_id", sa.Integer(), sa.ForeignKey("knowledgebases.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("normalized", sa.String(length=255), nullable=False),
            sa.Column("entity_type", sa.String(length=64), nullable=True),
            sa.Column("aliases", sa.JSON(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        )
        op.create_index(
            "uq_kb_entity_norm",
            "knowledge_graph_nodes",
            ["knowledge_base_id", "normalized"],
            unique=True,
        )

    if "knowledge_graph_edges" not in tables:
        op.create_table(
            "knowledge_graph_edges",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("knowledge_base_id", sa.Integer(), sa.ForeignKey("knowledgebases.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_node_id", sa.Integer(), sa.ForeignKey("knowledge_graph_nodes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("target_node_id", sa.Integer(), sa.ForeignKey("knowledge_graph_nodes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("relation", sa.String(length=128), nullable=False),
            sa.Column("weight", sa.Float(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(
            "uq_kb_edge_relation",
            "knowledge_graph_edges",
            ["knowledge_base_id", "source_node_id", "target_node_id", "relation"],
            unique=True,
        )

    if "knowledge_graph_evidence" not in tables:
        op.create_table(
            "knowledge_graph_evidence",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("knowledge_base_id", sa.Integer(), sa.ForeignKey("knowledgebases.id", ondelete="CASCADE"), nullable=False),
            sa.Column("node_id", sa.Integer(), sa.ForeignKey("knowledge_graph_nodes.id", ondelete="CASCADE"), nullable=True),
            sa.Column("edge_id", sa.Integer(), sa.ForeignKey("knowledge_graph_edges.id", ondelete="CASCADE"), nullable=True),
            sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("chunk_id", sa.String(length=128), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("evidence_text", sa.Text(), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(
            "idx_kb_node_edge",
            "knowledge_graph_evidence",
            ["knowledge_base_id", "node_id", "edge_id"],
            unique=False,
        )
        op.create_index(
            "idx_kb_doc_chunk",
            "knowledge_graph_evidence",
            ["knowledge_base_id", "document_id", "chunk_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "knowledge_graph_evidence" in tables:
        op.drop_index("idx_kb_doc_chunk", table_name="knowledge_graph_evidence")
        op.drop_index("idx_kb_node_edge", table_name="knowledge_graph_evidence")
        op.drop_table("knowledge_graph_evidence")

    if "knowledge_graph_edges" in tables:
        op.drop_index("uq_kb_edge_relation", table_name="knowledge_graph_edges")
        op.drop_table("knowledge_graph_edges")

    if "knowledge_graph_nodes" in tables:
        op.drop_index("uq_kb_entity_norm", table_name="knowledge_graph_nodes")
        op.drop_table("knowledge_graph_nodes")

    existing_cols = {col["name"] for col in inspector.get_columns("knowledgebases")}
    if "rag_config" in existing_cols:
        op.drop_column("knowledgebases", "rag_config")
    if "rag_provider" in existing_cols:
        op.drop_column("knowledgebases", "rag_provider")
