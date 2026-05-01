"""add pgvector long-term memory facts table

Revision ID: 21_add_pgvector_ltm_facts
Revises: 20_add_pgvector_rag_chunks
Create Date: 2026-05-01

"""

from typing import Sequence, Union

from alembic import op


revision: str = "21_add_pgvector_ltm_facts"
down_revision: Union[str, None] = "20_add_pgvector_rag_chunks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE ltm_facts (
            id BIGSERIAL PRIMARY KEY,
            fact_id TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            fact TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            embedding vector(1024) NOT NULL,
            importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            access_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_ltm_facts_user_id ON ltm_facts (user_id)")
    op.execute("CREATE INDEX idx_ltm_facts_session_id ON ltm_facts (session_id)")
    op.execute(
        """
        CREATE INDEX idx_ltm_facts_embedding_hnsw
        ON ltm_facts
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ltm_facts")
