"""Persist document-processing jobs.

Revision ID: c93e842bd560
Revises: b81d732ac459
"""
from alembic import op
import sqlalchemy as sa

revision = "c93e842bd560"
down_revision = "b81d732ac459"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("processing_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(20), server_default="queued", nullable=False),
        sa.Column("stage", sa.String(20), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed')", name="ck_processing_jobs_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_processing_jobs_attempts"),
    )
    op.create_index("ix_processing_jobs_document_id", "processing_jobs", ["document_id"])
    op.create_index("uq_processing_jobs_active", "processing_jobs", ["document_id"], unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"))


def downgrade():
    op.drop_table("processing_jobs")
