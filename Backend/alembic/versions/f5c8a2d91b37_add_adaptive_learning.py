"""Add topics, source-backed practice questions, quiz attempts and study plans.

Revision ID: f5c8a2d91b37
Revises: e7b2c3d4f501
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f5c8a2d91b37"
down_revision = "e7b2c3d4f501"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def created_at():
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade():
    op.create_table(
        "topics",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("course_id", UUID, sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        created_at(),
        sa.UniqueConstraint("course_id", "name", name="uq_topics_course_name"),
    )
    op.create_index("ix_topics_course_id", "topics", ["course_id"])

    op.create_table(
        "quiz_questions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", UUID, sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID, sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", UUID, sa.ForeignKey("document_chunks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_location", sa.JSON(), nullable=True),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("correct_index", sa.Integer(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(10), server_default="manual", nullable=False),
        sa.Column("reviewed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("visibility", sa.String(10), server_default="private", nullable=False),
        created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("visibility IN ('private', 'course')", name="ck_quiz_questions_visibility"),
        sa.CheckConstraint("origin IN ('manual', 'generated')", name="ck_quiz_questions_origin"),
        sa.CheckConstraint("correct_index >= 0", name="ck_quiz_questions_correct_index"),
    )
    for column in ["owner_id", "course_id", "topic_id", "document_id"]:
        op.create_index(f"ix_quiz_questions_{column}", "quiz_questions", [column])

    op.create_table(
        "quiz_attempts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", UUID, sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID, sa.ForeignKey("topics.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), server_default="in_progress", nullable=False),
        sa.Column("correct_count", sa.Integer(), nullable=True),
        sa.Column("total_count", sa.Integer(), nullable=False),
        created_at(),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('in_progress', 'submitted')", name="ck_quiz_attempts_status"),
    )
    op.create_index("ix_quiz_attempts_user_id", "quiz_attempts", ["user_id"])
    op.create_index("ix_quiz_attempts_course_id", "quiz_attempts", ["course_id"])

    op.create_table(
        "quiz_attempt_answers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("attempt_id", UUID, sa.ForeignKey("quiz_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("question_id", UUID, sa.ForeignKey("quiz_questions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("topic_id", UUID, sa.ForeignKey("topics.id", ondelete="SET NULL"), nullable=True),
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("correct_index", sa.Integer(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("source_location", sa.JSON(), nullable=True),
        sa.Column("chosen_index", sa.Integer(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("attempt_id", "position", name="uq_quiz_attempt_answers_position"),
    )
    op.create_index("ix_quiz_attempt_answers_attempt_id", "quiz_attempt_answers", ["attempt_id"])
    op.create_index("ix_quiz_attempt_answers_question", "quiz_attempt_answers", ["question_id"])
    op.create_index("ix_quiz_attempt_answers_topic_id", "quiz_attempt_answers", ["topic_id"])

    op.create_table(
        "study_plan_settings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", UUID, sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exam_date", sa.Date(), nullable=True),
        sa.Column("weekday_minutes", sa.JSON(), nullable=False),
        sa.Column("topic_difficulty", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "course_id", name="uq_study_plan_settings_user_course"),
    )
    op.create_index("ix_study_plan_settings_user_id", "study_plan_settings", ["user_id"])

    op.create_table(
        "study_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", UUID, sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID, sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(400), nullable=False),
        sa.Column("status", sa.String(10), server_default="planned", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        created_at(),
        sa.CheckConstraint("status IN ('planned', 'completed', 'missed')", name="ck_study_sessions_status"),
        sa.CheckConstraint("kind IN ('quiz', 'revision', 'practice', 'review')", name="ck_study_sessions_kind"),
        sa.CheckConstraint("minutes > 0", name="ck_study_sessions_minutes"),
    )
    op.create_index("ix_study_sessions_user_course_date", "study_sessions", ["user_id", "course_id", "session_date"])


def downgrade():
    for table in ["study_sessions", "study_plan_settings", "quiz_attempt_answers", "quiz_attempts", "quiz_questions", "topics"]:
        op.drop_table(table)
