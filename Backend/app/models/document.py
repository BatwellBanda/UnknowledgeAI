import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, func, BigInteger, Text, CheckConstraint, JSON, Float, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from database.connection import Base

class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("visibility IN ('private', 'course')", name="ck_documents_visibility"),
        CheckConstraint("visibility != 'course' OR course_id IS NOT NULL", name="ck_documents_shared_course"),
        CheckConstraint("media_kind IN ('audio', 'video')", name="ck_documents_media_kind"),
        CheckConstraint("media_duration_seconds >= 0", name="ck_documents_media_duration"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="RESTRICT"), nullable=True, index=True)
    visibility = Column(String(10), nullable=False, default="private", server_default="private")
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False, unique=True)
    file_type = Column(String(20), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False)
    processing_status = Column(String(20), nullable=False, default="uploaded")
    extracted_text = Column(Text, nullable=True)
    extracted_segments = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Recordings only: audio or video, measured length, and where the transcript came from.
    media_kind = Column(String(10), nullable=True)
    media_duration_seconds = Column(Float, nullable=True)
    transcript_source = Column(String(120), nullable=True)
    # Each transcript edit bumps the revision; processing records the revision it indexed.
    transcript_revision = Column(Integer, nullable=False, default=0, server_default="0")
    indexed_transcript_revision = Column(Integer, nullable=False, default=0, server_default="0")

    owner = relationship("User")
    course = relationship("Course")
