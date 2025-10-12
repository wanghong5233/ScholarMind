"""add is_ephemeral flag to knowledgebases

Revision ID: 6_add_is_ephemeral_kb
Revises: 5_cleanup_sessions_duplicate_fk
Create Date: 2025-10-11
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6_add_is_ephemeral_kb'
down_revision = '5_cleanup_sessions_duplicate_fk'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col['name'] for col in inspector.get_columns('knowledgebases')}
    if 'is_ephemeral' not in existing_cols:
        op.add_column('knowledgebases', sa.Column('is_ephemeral', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col['name'] for col in inspector.get_columns('knowledgebases')}
    if 'is_ephemeral' in existing_cols:
        op.drop_column('knowledgebases', 'is_ephemeral')


