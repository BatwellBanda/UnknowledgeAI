"""Preserve extraction segments and chunk source locations.

Revision ID: b81d732ac459
Revises: a62c941fe308
"""
from alembic import op
import sqlalchemy as sa

revision = "b81d732ac459"
down_revision = "a62c941fe308"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("extracted_segments", sa.JSON(), nullable=True))
    op.add_column("document_chunks", sa.Column("source_location", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("document_chunks", "source_location")
    op.drop_column("documents", "extracted_segments")
