import uuid
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from database.connection import Base


def utc_now():
    return datetime.now(timezone.utc)


class TranscriptSegment(Base):
    """One timed line of a recording's transcript; editable by the recording's owner."""
    __tablename__ = "transcript_segments"
    __table_args__ = (
        UniqueConstraint("document_id", "position", name="uq_transcript_segments_position"),
        CheckConstraint("position >= 0", name="ck_transcript_segments_position"),
        CheckConstraint("start_seconds >= 0 AND end_seconds >= start_seconds", name="ck_transcript_segments_times"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    start_seconds = Column(Float, nullable=False)
    end_seconds = Column(Float, nullable=False)
    text = Column(Text, nullable=False)
    edited_at = Column(DateTime(timezone=True), nullable=True)


class Note(Base):
    """A private study note on a material, optionally pinned to a moment in a recording."""
    __tablename__ = "notes"
    __table_args__ = (
        CheckConstraint("timestamp_seconds IS NULL OR timestamp_seconds >= 0", name="ck_notes_timestamp"),
        Index("ix_notes_owner_document", "owner_id", "document_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    timestamp_seconds = Column(Float, nullable=True)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
