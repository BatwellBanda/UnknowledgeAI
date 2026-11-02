import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, func, BigInteger, Text, CheckConstraint, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from database.connection import Base

class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("visibility IN ('private', 'course')", name="ck_documents_visibility"),
        CheckConstraint("visibility != 'course' OR course_id IS NOT NULL", name="ck_documents_shared_course"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
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
    
    owner = relationship("User")
    course = relationship("Course")
