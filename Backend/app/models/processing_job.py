import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, CheckConstraint, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from database.connection import Base


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'completed', 'failed')", name="ck_processing_jobs_status"),
        CheckConstraint("attempts >= 0", name="ck_processing_jobs_attempts"),
        Index("uq_processing_jobs_active", "document_id", unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')")),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="queued", server_default="queued")
    stage = Column(String(20), nullable=False, default="queued", server_default="queued")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    error_message = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
