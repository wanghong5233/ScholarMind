"""add rolling_summary to sessions

Revision ID: 8_add_rolling_summary
Revises: 7_add_defaults_json
Create Date: 2025-10-12
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8_add_rolling_summary'
down_revision = '7_add_defaults_json'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col['name'] for col in inspector.get_columns('sessions')}
    if 'rolling_summary' not in existing_cols:
        op.add_column('sessions', sa.Column('rolling_summary', sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col['name'] for col in inspector.get_columns('sessions')}
    if 'rolling_summary' in existing_cols:
        op.drop_column('sessions', 'rolling_summary')


