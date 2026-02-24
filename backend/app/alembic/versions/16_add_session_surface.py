"""add session surface for conversation isolation

Revision ID: 16_add_session_surface
Revises: 15_session_summary_checkpoints
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "16_add_session_surface"
down_revision: Union[str, None] = "15_session_summary_checkpoints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("sessions")}

    if "surface" not in existing_cols:
        op.add_column(
            "sessions",
            sa.Column(
                "surface",
                sa.String(length=32),
                nullable=False,
                server_default="deep_chat",
            ),
        )
        op.execute(
            sa.text(
                """
                UPDATE sessions
                   SET surface = 'deep_chat'
                 WHERE surface IS NULL
                    OR trim(surface) = ''
                """
            )
        )

    indexes = {idx["name"] for idx in inspector.get_indexes("sessions")}
    if "idx_sessions_user_surface" not in indexes:
        op.create_index(
            "idx_sessions_user_surface",
            "sessions",
            ["user_id", "surface"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {idx["name"] for idx in inspector.get_indexes("sessions")}
    if "idx_sessions_user_surface" in indexes:
        op.drop_index("idx_sessions_user_surface", table_name="sessions")

    existing_cols = {col["name"] for col in inspector.get_columns("sessions")}
    if "surface" in existing_cols:
        op.drop_column("sessions", "surface")
