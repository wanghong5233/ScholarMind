"""add index on messages(session_id, create_time)

Revision ID: 9_idx_messages_session_time
Revises: 8_add_rolling_summary
Create Date: 2025-10-12
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9_idx_messages_session_time'
down_revision = '8_add_rolling_summary'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = [ix['name'] for ix in inspector.get_indexes('messages')]
    if 'ix_messages_session_time' not in existing:
        op.create_index('ix_messages_session_time', 'messages', ['session_id', 'create_time'])


def downgrade() -> None:
    op.drop_index('ix_messages_session_time', table_name='messages')


