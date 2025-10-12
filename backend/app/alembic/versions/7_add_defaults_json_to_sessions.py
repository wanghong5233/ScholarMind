"""add defaults_json to sessions

Revision ID: 7_add_defaults_json
Revises: 6_add_is_ephemeral_kb
Create Date: 2025-10-11
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7_add_defaults_json'
down_revision = '6_add_is_ephemeral_kb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col['name'] for col in inspector.get_columns('sessions')}
    if 'defaults_json' not in existing_cols:
        op.add_column('sessions', sa.Column('defaults_json', sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col['name'] for col in inspector.get_columns('sessions')}
    if 'defaults_json' in existing_cols:
        op.drop_column('sessions', 'defaults_json')


