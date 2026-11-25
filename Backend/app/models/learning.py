"""Topics, source-backed practice questions, quiz attempts and study plans."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from database.connection import Base


def utc_now():
    return datetime.now(timezone.utc)


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("course_id", "name", name="uq_topics_course_name"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    __table_args__ = (
        CheckConstraint("visibility IN ('private', 'course')", name="ck_quiz_questions_visibility"),
        CheckConstraint("origin IN ('manual', 'generated')", name="ck_quiz_questions_origin"),
        CheckConstraint("correct_index >= 0", name="ck_quiz_questions_correct_index"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True)
    # The source document governs who may see the question; removing it removes the question.
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    # Reprocessing replaces chunks; the quote is used to re-anchor the question afterwards.
    chunk_id = Column(UUID(as_uuid=True), ForeignKey("document_chunks.id", ondelete="SET NULL"), nullable=True)
    source_location = Column(JSON, nullable=True)
    source_quote = Column(Text, nullable=False)
    stem = Column(Text, nullable=False)
    options = Column(JSON, nullable=False)
    correct_index = Column(Integer, nullable=False)
    explanation = Column(Text, nullable=False)
    origin = Column(String(10), nullable=False, default="manual", server_default="manual")
    reviewed = Column(Boolean, nullable=False, default=False, server_default="false")
    visibility = Column(String(10), nullable=False, default="private", server_default="private")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now())

    topic = relationship("Topic")
    document = relationship("Document")


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    __table_args__ = (CheckConstraint("status IN ('in_progress', 'submitted')", name="ck_quiz_attempts_status"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(20), nullable=False, default="in_progress", server_default="in_progress")
    correct_count = Column(Integer, nullable=True)
    total_count = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    submitted_at = Column(DateTime(timezone=True), nullable=True)

    answers = relationship("QuizAttemptAnswer", order_by="QuizAttemptAnswer.position", cascade="all, delete-orphan")


class QuizAttemptAnswer(Base):
    """A snapshot of the question as asked, so later edits never change a past grade."""
    __tablename__ = "quiz_attempt_answers"
    __table_args__ = (
        UniqueConstraint("attempt_id", "position", name="uq_quiz_attempt_answers_position"),
        Index("ix_quiz_attempt_answers_question", "question_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attempt_id = Column(UUID(as_uuid=True), ForeignKey("quiz_attempts.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    question_id = Column(UUID(as_uuid=True), ForeignKey("quiz_questions.id", ondelete="SET NULL"), nullable=True)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True)
    stem = Column(Text, nullable=False)
    options = Column(JSON, nullable=False)
    correct_index = Column(Integer, nullable=False)
    explanation = Column(Text, nullable=False)
    source_quote = Column(Text, nullable=False)
    source_location = Column(JSON, nullable=True)
    chosen_index = Column(Integer, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    answered_at = Column(DateTime(timezone=True), nullable=True)

    question = relationship("QuizQuestion")


class StudyPlanSettings(Base):
    __tablename__ = "study_plan_settings"
    __table_args__ = (UniqueConstraint("user_id", "course_id", name="uq_study_plan_settings_user_course"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    exam_date = Column(Date, nullable=True)
    # Minutes available Monday..Sunday, after classes and other commitments.
    weekday_minutes = Column(JSON, nullable=False)
    # {topic_id: 1 (easy) | 2 (normal) | 3 (hard)} as rated by the student.
    topic_difficulty = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now())


class StudySession(Base):
    __tablename__ = "study_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('planned', 'completed', 'missed')", name="ck_study_sessions_status"),
        CheckConstraint("kind IN ('quiz', 'revision', 'practice', 'review')", name="ck_study_sessions_kind"),
        CheckConstraint("minutes > 0", name="ck_study_sessions_minutes"),
        Index("ix_study_sessions_user_course_date", "user_id", "course_id", "session_date"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    session_date = Column(Date, nullable=False)
    minutes = Column(Integer, nullable=False)
    kind = Column(String(10), nullable=False)
    reason = Column(String(400), nullable=False)
    status = Column(String(10), nullable=False, default="planned", server_default="planned")
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())

    topic = relationship("Topic")
