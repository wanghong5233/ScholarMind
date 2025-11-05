"""add memories table and stm columns

Revision ID: 11_memories_stm
Revises: 10_fk_messages_session_id
Create Date: 2025-10-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "11_memories_stm"
down_revision = "10_fk_messages_session_id"
branch_labels = None
depends_on = None


def _column_names(inspector, table_name: str) -> set[str]:
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- messages table extensions ---
    message_cols = _column_names(inspector, "messages")
    if "user_summary" not in message_cols:
        op.add_column("messages", sa.Column("user_summary", sa.Text(), nullable=True))
    if "assistant_summary" not in message_cols:
        op.add_column("messages", sa.Column("assistant_summary", sa.Text(), nullable=True))
    if "user_embedding" not in message_cols:
        op.add_column("messages", sa.Column("user_embedding", sa.JSON(), nullable=True))
    if "assistant_embedding" not in message_cols:
        op.add_column("messages", sa.Column("assistant_embedding", sa.JSON(), nullable=True))

    # --- sessions table extensions ---
    session_cols = _column_names(inspector, "sessions")
    if "memory_guide_fail_count" not in session_cols:
        op.add_column("sessions", sa.Column("memory_guide_fail_count", sa.Integer(), nullable=False, server_default="0"))
        op.alter_column("sessions", "memory_guide_fail_count", server_default=None)
    if "memory_guide_disabled" not in session_cols:
        op.add_column("sessions", sa.Column("memory_guide_disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")))
        op.alter_column("sessions", "memory_guide_disabled", server_default=None)

    # --- memories table ---
    if "memories" not in inspector.get_table_names():
        op.create_table(
            "memories",
            sa.Column("memory_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("session_id", sa.String(length=16), nullable=True),
            sa.Column("memory_type", sa.String(length=32), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("document_id", sa.String(length=64), nullable=True),
            sa.Column("meta_data", sa.JSON(), nullable=True),
            sa.Column("importance", sa.Float(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("embedding", sa.JSON(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_accessed", sa.TIMESTAMP(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        )
        op.create_index("idx_memories_user_id", "memories", ["user_id"])
        op.create_index("idx_memories_session_id", "memories", ["session_id"])
        op.create_index("idx_memories_document_id", "memories", ["document_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "memories" in inspector.get_table_names():
        op.drop_index("idx_memories_document_id", table_name="memories")
        op.drop_index("idx_memories_session_id", table_name="memories")
        op.drop_index("idx_memories_user_id", table_name="memories")
        op.drop_table("memories")

    message_cols = _column_names(inspector, "messages")
    if "assistant_embedding" in message_cols:
        op.drop_column("messages", "assistant_embedding")
    if "user_embedding" in message_cols:
        op.drop_column("messages", "user_embedding")
    if "assistant_summary" in message_cols:
        op.drop_column("messages", "assistant_summary")
    if "user_summary" in message_cols:
        op.drop_column("messages", "user_summary")

    session_cols = _column_names(inspector, "sessions")
    if "memory_guide_disabled" in session_cols:
        op.drop_column("sessions", "memory_guide_disabled")
    if "memory_guide_fail_count" in session_cols:
        op.drop_column("sessions", "memory_guide_fail_count")

