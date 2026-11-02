"""Require course join codes and support server-side token revocation.

Revision ID: d4a1f0c2e731
Revises: c93e842bd560
"""
from alembic import op
import sqlalchemy as sa
from app.models.course import new_join_code

revision = "d4a1f0c2e731"
down_revision = "c93e842bd560"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("token_version", sa.Integer(), server_default="0", nullable=False))
    op.add_column("courses", sa.Column("join_code", sa.String(16), nullable=True))
    connection = op.get_bind()
    # Existing courses get their own random code; instructors can rotate it later.
    for (course_id,) in connection.execute(sa.text("SELECT id FROM courses")).fetchall():
        connection.execute(sa.text("UPDATE courses SET join_code = :code WHERE id = :id"), {"code": new_join_code(), "id": course_id})
    op.alter_column("courses", "join_code", nullable=False)


def downgrade():
    op.drop_column("courses", "join_code")
    op.drop_column("users", "token_version")
