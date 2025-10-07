"""
add jobs table

Revision ID: 2_add_jobs_table
Revises: 1c50227d83cc
Create Date: 2025-10-07
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2_add_jobs_table'
down_revision = '1c50227d83cc'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'jobs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('knowledge_base_id', sa.Integer(), sa.ForeignKey('knowledgebases.id', ondelete='CASCADE'), nullable=False),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='pending'),
        sa.Column('progress', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('succeeded', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.TIMESTAMP(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_jobs_user_id', 'jobs', ['user_id'])
    op.create_index('idx_jobs_kb_id', 'jobs', ['knowledge_base_id'])


def downgrade() -> None:
    op.drop_index('idx_jobs_kb_id', table_name='jobs')
    op.drop_index('idx_jobs_user_id', table_name='jobs')
    op.drop_table('jobs')


