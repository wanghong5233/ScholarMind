"""placeholder for legacy revision: 3_add_session_kb_and_focus_table

This migration bridges an existing database revision id that no longer has a
matching file in the codebase. It performs no schema changes.

Revision ID: 3_add_session_kb_and_focus_table
Revises: 2_add_jobs_table
Create Date: 2025-10-11
"""

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


# revision identifiers, used by Alembic.
revision = '3_add_session_kb_and_focus_table'
down_revision = '2_add_jobs_table'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: this placeholder exists to align with an existing DB revision.
    pass


def downgrade() -> None:
    # No-op: nothing to downgrade for the placeholder.
    pass


