"""add_structure_metadata_to_documents

Revision ID: 13_add_structure_metadata
Revises: 12_add_context_json
Create Date: 2025-11-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "13_add_structure_metadata"
down_revision: Union[str, None] = "12_add_context_json"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("structure_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "structure_metadata")

