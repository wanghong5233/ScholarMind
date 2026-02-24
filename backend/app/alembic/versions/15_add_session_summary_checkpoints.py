"""add session_summary_checkpoints table

Revision ID: 15_session_summary_checkpoints
Revises: 14_add_rag_provider_graph
Create Date: 2026-02-17

可选表：记录 rolling_summary 每次更新时的 checkpoint，便于回溯与调试。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic. 保持 <=32 字符以兼容 alembic_version.version_num
revision: str = "15_session_summary_checkpoints"
down_revision: Union[str, None] = "14_add_rag_provider_graph"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "session_summary_checkpoints" in tables:
        return

    op.create_table(
        "session_summary_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.String(length=16),
            sa.ForeignKey("sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.message_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_session_summary_checkpoints_session_id",
        "session_summary_checkpoints",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_session_summary_checkpoints_session_id",
        table_name="session_summary_checkpoints",
    )
    op.drop_table("session_summary_checkpoints")
