"""add context_json to sessions

Revision ID: 12_add_context_json
Revises: 11_memories_stm
Create Date: 2025-11-10
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '12_add_context_json'
down_revision = '11_memories_stm'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col['name'] for col in inspector.get_columns('sessions')}
    if 'context_json' not in existing_cols:
        op.add_column('sessions', sa.Column('context_json', sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col['name'] for col in inspector.get_columns('sessions')}
    if 'context_json' in existing_cols:
        op.drop_column('sessions', 'context_json')

