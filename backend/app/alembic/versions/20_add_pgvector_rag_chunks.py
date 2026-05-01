"""add pgvector rag_chunks table

Revision ID: 20_add_pgvector_rag_chunks
Revises: 19_add_demo_access_logs
Create Date: 2026-05-01

"""

from typing import Sequence, Union

from alembic import op


revision: str = "20_add_pgvector_rag_chunks"
down_revision: Union[str, None] = "19_add_demo_access_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE rag_chunks (
            id BIGSERIAL PRIMARY KEY,
            index_name TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            kb_id INTEGER NOT NULL REFERENCES knowledgebases(id) ON DELETE CASCADE,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            session_id VARCHAR(16) NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            scope VARCHAR(16) NOT NULL DEFAULT 'global',
            text TEXT NOT NULL,
            embedding vector(1024),
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            chunk_index INTEGER NOT NULL DEFAULT 0,
            prev_chunk_id TEXT,
            next_chunk_id TEXT,
            element_type VARCHAR(128),
            parser_engine VARCHAR(64),
            source VARCHAR(64),
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            updated_at TIMESTAMP NOT NULL DEFAULT now(),
            CONSTRAINT ck_rag_chunks_scope CHECK (scope IN ('global', 'session'))
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_rag_chunks_index_chunk ON rag_chunks (index_name, chunk_id)")
    op.execute("CREATE INDEX idx_rag_chunks_index_kb ON rag_chunks (index_name, kb_id)")
    op.execute("CREATE INDEX idx_rag_chunks_kb_doc ON rag_chunks (kb_id, document_id)")
    op.execute("CREATE INDEX idx_rag_chunks_scope_session ON rag_chunks (scope, session_id)")
    op.execute("CREATE INDEX idx_rag_chunks_chunk_index ON rag_chunks (document_id, chunk_index)")
    op.execute("CREATE INDEX idx_rag_chunks_element_type ON rag_chunks (element_type)")
    op.execute("CREATE INDEX idx_rag_chunks_metadata_gin ON rag_chunks USING gin (metadata)")
    op.execute(
        """
        CREATE INDEX idx_rag_chunks_text_fts
        ON rag_chunks
        USING gin (to_tsvector('simple', coalesce(text, '')))
        """
    )
    op.execute(
        """
        CREATE INDEX idx_rag_chunks_embedding_hnsw
        ON rag_chunks
        USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_chunks")
