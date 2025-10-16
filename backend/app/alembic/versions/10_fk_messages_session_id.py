"""add FK messages.session_id -> sessions.session_id

Revision ID: 10_fk_messages_session_id
Revises: 9_idx_messages_session_time
Create Date: 2025-10-12
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '10_fk_messages_session_id'
down_revision = '9_idx_messages_session_time'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # 确保 sessions 存在主键 session_id
    cols = {c['name'] for c in inspector.get_columns('messages')}
    if 'session_id' in cols:
        fks = inspector.get_foreign_keys('messages')
        names = {fk.get('name') for fk in fks}
        if 'fk_messages_session_id' not in (names or set()):
            op.create_foreign_key('fk_messages_session_id', 'messages', 'sessions', ['session_id'], ['session_id'], ondelete='CASCADE')


def downgrade() -> None:
    try:
        op.drop_constraint('fk_messages_session_id', 'messages', type_='foreignkey')
    except Exception:
        pass


