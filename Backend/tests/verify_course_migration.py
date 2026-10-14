"""Opt-in PostgreSQL migration check in a fresh, transaction-scoped schema.

Run from Backend: python tests/verify_course_migration.py
Requires the configured PostgreSQL server and its installed vector extension.
The transaction is rolled back; no development tables or records are altered.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.config import Config
from sqlalchemy import text, inspect
from sqlalchemy.exc import IntegrityError
from database.connection import engine


def verify():
    schema = "course_migration_test_" + uuid.uuid4().hex
    assert schema.startswith("course_migration_test_") and schema.isidentifier()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            # Generated schema name has only letters, underscores, and hexadecimal digits.
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            config.attributes["version_table_schema"] = schema
            command.upgrade(config, "5e28aec281c2")
            assert {"roles", "users", "documents", "document_chunks", "alembic_version"}.issubset(
                set(inspect(connection).get_table_names(schema=schema))
            ), "Migration tables must exist in the isolated schema before inserting fixtures"
            role_id, user_id, document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            connection.execute(text("INSERT INTO roles (id, name) VALUES (:id, 'STUDENT')"), {"id": role_id})
            connection.execute(text("INSERT INTO users (id, full_name, email, hashed_password, role_id) VALUES (:id, 'Migration test', 'migration@example.com', 'fixture', :role)"), {"id": user_id, "role": role_id})
            connection.execute(text("INSERT INTO documents (id, owner_id, original_filename, stored_filename, file_type, file_size_bytes, processing_status, extracted_text) VALUES (:id, :owner, 'old.txt', 'old.txt', 'txt', 3, 'extracted', 'old')"), {"id": document_id, "owner": user_id})
            command.upgrade(config, "head")
            record = connection.execute(text("SELECT id, visibility, course_id, extracted_text FROM documents WHERE id = :id"), {"id": document_id}).one()
            assert record.id == document_id and record.visibility == "private" and record.course_id is None and record.extracted_text == "old"
            course_id, enrollment_id = uuid.uuid4(), uuid.uuid4()
            connection.execute(text("INSERT INTO courses (id, code, title, instructor_id) VALUES (:id, 'TEST101', 'Test course', :instructor)"), {"id": course_id, "instructor": user_id})
            connection.execute(text("INSERT INTO enrollments (id, course_id, user_id) VALUES (:id, :course, :user)"), {"id": enrollment_id, "course": course_id, "user": user_id})
            violations = [
                ("INSERT INTO enrollments (id, course_id, user_id) VALUES (:id, :course, :user)", {"id": uuid.uuid4(), "course": course_id, "user": user_id}),
                ("UPDATE documents SET visibility = 'course' WHERE id = :id", {"id": document_id}),
                ("UPDATE documents SET visibility = 'everyone' WHERE id = :id", {"id": document_id}),
                ("UPDATE documents SET course_id = :course WHERE id = :id", {"id": document_id, "course": uuid.uuid4()}),
            ]
            for statement, parameters in violations:
                try:
                    with connection.begin_nested():
                        connection.execute(text(statement), parameters)
                except IntegrityError:
                    pass
                else:
                    raise AssertionError("Database accepted an invalid enrollment or document")
            command.downgrade(config, "5e28aec281c2")
            assert connection.scalar(text("SELECT extracted_text FROM documents WHERE id = :id"), {"id": document_id}) == "old"
            assert "courses" not in inspect(connection).get_table_names(schema=schema)
            command.upgrade(config, "head")
            assert connection.scalar(text("SELECT visibility FROM documents WHERE id = :id"), {"id": document_id}) == "private"
            print("PostgreSQL migration upgrade/downgrade/re-upgrade, legacy document preservation, and constraints: passed")
        finally:
            transaction.rollback()
            print("Temporary migration schema rolled back; development records unchanged")


if __name__ == "__main__":
    verify()
