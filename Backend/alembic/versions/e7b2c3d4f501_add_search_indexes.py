"""Index chunks by document so permission-filtered search scans only readable chunks.

An approximate (HNSW) index is deliberately not used: it ranks the whole table before the
per-user permission filter, which can drop a user's matches from the candidate set.

Revision ID: e7b2c3d4f501
Revises: d4a1f0c2e731
"""
from alembic import op

revision = "e7b2c3d4f501"
down_revision = "d4a1f0c2e731"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])


def downgrade():
    op.drop_index("ix_documents_owner_id", table_name="documents")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
