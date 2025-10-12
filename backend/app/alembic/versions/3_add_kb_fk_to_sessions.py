"""add knowledge_base_id fk to sessions

Revision ID: 3_add_kb_fk_to_sessions
Revises: 2_add_jobs_table
Create Date: 2025-10-11
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4_add_kb_fk_to_sessions'
down_revision = '3_add_session_kb_and_focus_table'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1) 若列不存在则新增列（可空，向后兼容）
    existing_cols = {col['name'] for col in inspector.get_columns('sessions')}
    if 'knowledge_base_id' not in existing_cols:
        op.add_column('sessions', sa.Column('knowledge_base_id', sa.Integer(), nullable=True))

    # 2) 若外键不存在则创建外键
    existing_fks = {fk.get('name') for fk in inspector.get_foreign_keys('sessions')}
    if 'fk_sessions_knowledge_base_id' not in existing_fks:
        op.create_foreign_key(
            constraint_name='fk_sessions_knowledge_base_id',
            source_table='sessions',
            referent_table='knowledgebases',
            local_cols=['knowledge_base_id'],
            remote_cols=['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 删除外键（若存在）
    existing_fks = {fk.get('name') for fk in inspector.get_foreign_keys('sessions')}
    if 'fk_sessions_knowledge_base_id' in existing_fks:
        op.drop_constraint('fk_sessions_knowledge_base_id', 'sessions', type_='foreignkey')

    # 删除列（若存在）
    existing_cols = {col['name'] for col in inspector.get_columns('sessions')}
    if 'knowledge_base_id' in existing_cols:
        op.drop_column('sessions', 'knowledge_base_id')


