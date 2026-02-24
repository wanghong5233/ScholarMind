"""add user role and admin audit logs

Revision ID: 17_admin_role_audit_log
Revises: 16_add_session_surface
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "17_admin_role_audit_log"
down_revision: Union[str, None] = "16_add_session_surface"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    user_columns = {col["name"] for col in inspector.get_columns("users")}
    if "role" not in user_columns:
        op.add_column(
            "users",
            sa.Column(
                "role",
                sa.String(length=32),
                nullable=False,
                server_default="user",
            ),
        )
        op.execute(
            sa.text(
                """
                UPDATE users
                   SET role = 'user'
                 WHERE role IS NULL
                    OR trim(role) = ''
                """
            )
        )

    tables = inspector.get_table_names()
    if "admin_audit_logs" not in tables:
        op.create_table(
            "admin_audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "admin_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("target_type", sa.String(length=64), nullable=False),
            sa.Column("target_id", sa.String(length=128), nullable=True),
            sa.Column("detail_json", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.TIMESTAMP(),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "idx_admin_audit_logs_created_at",
            "admin_audit_logs",
            ["created_at"],
            unique=False,
        )
        op.create_index(
            "idx_admin_audit_logs_admin_user_id",
            "admin_audit_logs",
            ["admin_user_id"],
            unique=False,
        )
        op.create_index(
            "idx_admin_audit_logs_action",
            "admin_audit_logs",
            ["action"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "admin_audit_logs" in tables:
        indexes = {idx["name"] for idx in inspector.get_indexes("admin_audit_logs")}
        if "idx_admin_audit_logs_created_at" in indexes:
            op.drop_index(
                "idx_admin_audit_logs_created_at",
                table_name="admin_audit_logs",
            )
        if "idx_admin_audit_logs_admin_user_id" in indexes:
            op.drop_index(
                "idx_admin_audit_logs_admin_user_id",
                table_name="admin_audit_logs",
            )
        if "idx_admin_audit_logs_action" in indexes:
            op.drop_index(
                "idx_admin_audit_logs_action",
                table_name="admin_audit_logs",
            )
        op.drop_table("admin_audit_logs")

    user_columns = {col["name"] for col in inspector.get_columns("users")}
    if "role" in user_columns:
        op.drop_column("users", "role")
