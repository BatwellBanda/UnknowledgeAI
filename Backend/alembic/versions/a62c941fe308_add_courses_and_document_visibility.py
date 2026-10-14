"""Add courses, enrolments, and explicit document visibility.

Revision ID: a62c941fe308
Revises: 5e28aec281c2
"""
from alembic import op
import sqlalchemy as sa

revision = "a62c941fe308"
down_revision = "5e28aec281c2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "courses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("instructor_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.ForeignKeyConstraint(["instructor_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_courses_instructor_id", "courses", ["instructor_id"])
    op.create_table(
        "enrollments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("course_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("course_id", "user_id", name="uq_enrollments_course_user"),
    )
    op.create_index("ix_enrollments_course_id", "enrollments", ["course_id"])
    op.create_index("ix_enrollments_user_id", "enrollments", ["user_id"])
    op.add_column("documents", sa.Column("course_id", sa.UUID(), nullable=True))
    # Server default backfills existing documents as private.
    op.add_column("documents", sa.Column("visibility", sa.String(10), server_default="private", nullable=False))
    op.create_index("ix_documents_course_id", "documents", ["course_id"])
    op.create_foreign_key("fk_documents_course_id", "documents", "courses", ["course_id"], ["id"], ondelete="RESTRICT")
    op.create_check_constraint("ck_documents_visibility", "documents", "visibility IN ('private', 'course')")
    op.create_check_constraint("ck_documents_shared_course", "documents", "visibility != 'course' OR course_id IS NOT NULL")


def downgrade():
    op.drop_constraint("ck_documents_shared_course", "documents", type_="check")
    op.drop_constraint("ck_documents_visibility", "documents", type_="check")
    op.drop_constraint("fk_documents_course_id", "documents", type_="foreignkey")
    op.drop_index("ix_documents_course_id", table_name="documents")
    op.drop_column("documents", "visibility")
    op.drop_column("documents", "course_id")
    op.drop_table("enrollments")
    op.drop_table("courses")
