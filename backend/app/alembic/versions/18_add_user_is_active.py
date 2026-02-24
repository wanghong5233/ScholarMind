"""add is_active column to users

Revision ID: 18_add_user_is_active
Revises: 17_admin_role_audit_log
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "18_add_user_is_active"
down_revision: Union[str, None] = "17_admin_role_audit_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {col["name"] for col in inspector.get_columns("users")}
    if "is_active" not in user_columns:
        op.add_column(
            "users",
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )
        op.execute(
            sa.text(
                """
                UPDATE users
                   SET is_active = true
                 WHERE is_active IS NULL
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {col["name"] for col in inspector.get_columns("users")}
    if "is_active" in user_columns:
        op.drop_column("users", "is_active")

