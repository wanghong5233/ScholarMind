"""add_document_processing_status

Introduce a first-class processing lifecycle for documents so the KB list,
the job records and the actual RAG-readiness of a document share a single
source of truth (`documents.processing_status`).

Backfill rule for existing rows:
    - rag_chunks count > 0  ->  status = 'ready' (chunk_count = N)
    - rag_chunks count == 0 ->  status = 'failed', failure_stage = 'legacy'
                                (these are pre-existing ghost rows)

Revision ID: 22_add_doc_processing_status
Revises: 21_add_pgvector_ltm_facts
Create Date: 2026-05-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "22_add_doc_processing_status"
down_revision: Union[str, None] = "21_add_pgvector_ltm_facts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "processing_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
            comment="pending | parsing | ready | failed",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "chunk_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of rag_chunks rows for this doc (denormalised cache).",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "failure_stage",
            sa.String(length=32),
            nullable=True,
            comment="parse | chunk | embed | index | download | legacy",
        ),
    )
    op.add_column(
        "documents",
        sa.Column("failure_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("last_processed_at", sa.TIMESTAMP(), nullable=True),
    )
    op.create_index(
        "idx_documents_kb_processing_status",
        "documents",
        ["knowledge_base_id", "processing_status"],
    )

    # Backfill: derive status from existing rag_chunks. Anything with chunks is
    # 'ready', anything without chunks is a pre-existing ghost ('failed').
    op.execute(
        """
        UPDATE documents d
        SET chunk_count = sub.cnt,
            processing_status = CASE WHEN sub.cnt > 0 THEN 'ready' ELSE 'failed' END,
            failure_stage     = CASE WHEN sub.cnt > 0 THEN NULL    ELSE 'legacy' END,
            failure_reason    = CASE
                                  WHEN sub.cnt > 0 THEN NULL
                                  ELSE 'No rag_chunks found at migration time; legacy ghost row.'
                                END,
            last_processed_at = d.updated_at
        FROM (
            SELECT d2.id AS doc_id, COALESCE(c.cnt, 0) AS cnt
            FROM documents d2
            LEFT JOIN (
                SELECT document_id, COUNT(*) AS cnt
                FROM rag_chunks
                GROUP BY document_id
            ) c ON c.document_id = d2.id
        ) sub
        WHERE d.id = sub.doc_id;
        """
    )


def downgrade() -> None:
    op.drop_index("idx_documents_kb_processing_status", table_name="documents")
    op.drop_column("documents", "last_processed_at")
    op.drop_column("documents", "failure_reason")
    op.drop_column("documents", "failure_stage")
    op.drop_column("documents", "chunk_count")
    op.drop_column("documents", "processing_status")
