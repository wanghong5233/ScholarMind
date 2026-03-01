"""add demo_access_logs table

Revision ID: 19_add_demo_access_logs
Revises: 18_add_user_is_active
Create Date: 2026-02-24

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "19_add_demo_access_logs"
down_revision: Union[str, None] = "18_add_user_is_active"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "demo_access_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("visited_at", sa.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_demo_access_logs_ip", "demo_access_logs", ["ip"])
    op.create_index("idx_demo_access_logs_visited_at", "demo_access_logs", ["visited_at"])
    op.create_index("idx_demo_access_logs_ip_visited", "demo_access_logs", ["ip", "visited_at"])


def downgrade() -> None:
    op.drop_table("demo_access_logs")
