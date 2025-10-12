"""cleanup duplicate FK on sessions. keep fk_sessions_knowledge_base_id

Revision ID: 5_cleanup_sessions_duplicate_fk
Revises: 4_add_kb_fk_to_sessions
Create Date: 2025-10-11
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5_cleanup_sessions_duplicate_fk'
down_revision = '4_add_kb_fk_to_sessions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Ensure the desired FK exists; if missing (unlikely), recreate it
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

    # Drop the legacy/duplicate FK if it exists
    existing_fks = {fk.get('name') for fk in inspector.get_foreign_keys('sessions')}
    if 'fk_sessions_kb_id' in existing_fks:
        op.drop_constraint('fk_sessions_kb_id', 'sessions', type_='foreignkey')

    # We keep index 'idx_sessions_kb_id' for performance; no changes required


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Recreate legacy FK only if it does not exist
    existing_fks = {fk.get('name') for fk in inspector.get_foreign_keys('sessions')}
    if 'fk_sessions_kb_id' not in existing_fks:
        op.create_foreign_key(
            constraint_name='fk_sessions_kb_id',
            source_table='sessions',
            referent_table='knowledgebases',
            local_cols=['knowledge_base_id'],
            remote_cols=['id'],
            ondelete='SET NULL',
        )

    # Optionally drop the preferred FK to revert to old state if both exist
    existing_fks = {fk.get('name') for fk in inspector.get_foreign_keys('sessions')}
    if 'fk_sessions_knowledge_base_id' in existing_fks:
        op.drop_constraint('fk_sessions_knowledge_base_id', 'sessions', type_='foreignkey')


