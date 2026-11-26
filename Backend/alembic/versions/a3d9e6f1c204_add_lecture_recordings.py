"""Add lecture recordings: media metadata, timestamped transcripts and study notes.

Revision ID: a3d9e6f1c204
Revises: f5c8a2d91b37
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a3d9e6f1c204"
down_revision = "f5c8a2d91b37"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade():
    op.add_column("documents", sa.Column("media_kind", sa.String(10), nullable=True))
    op.add_column("documents", sa.Column("media_duration_seconds", sa.Float(), nullable=True))
    op.add_column("documents", sa.Column("transcript_source", sa.String(120), nullable=True))
    op.add_column("documents", sa.Column("transcript_revision", sa.Integer(), server_default="0", nullable=False))
    op.add_column("documents", sa.Column("indexed_transcript_revision", sa.Integer(), server_default="0", nullable=False))
    op.create_check_constraint("ck_documents_media_kind", "documents", "media_kind IN ('audio', 'video')")
    op.create_check_constraint("ck_documents_media_duration", "documents", "media_duration_seconds >= 0")

    op.create_table(
        "transcript_segments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("document_id", "position", name="uq_transcript_segments_position"),
        sa.CheckConstraint("position >= 0", name="ck_transcript_segments_position"),
        sa.CheckConstraint("start_seconds >= 0 AND end_seconds >= start_seconds", name="ck_transcript_segments_times"),
    )
    op.create_index("ix_transcript_segments_document_id", "transcript_segments", ["document_id"])

    op.create_table(
        "notes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp_seconds", sa.Float(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("timestamp_seconds IS NULL OR timestamp_seconds >= 0", name="ck_notes_timestamp"),
    )
    op.create_index("ix_notes_owner_document", "notes", ["owner_id", "document_id"])
    op.create_index("ix_notes_document_id", "notes", ["document_id"])


def downgrade():
    op.drop_table("notes")
    op.drop_table("transcript_segments")
    op.drop_constraint("ck_documents_media_duration", "documents", type_="check")
    op.drop_constraint("ck_documents_media_kind", "documents", type_="check")
    for column in ["indexed_transcript_revision", "transcript_revision", "transcript_source", "media_duration_seconds", "media_kind"]:
        op.drop_column("documents", column)
