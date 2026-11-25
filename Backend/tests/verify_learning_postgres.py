"""Opt-in PostgreSQL check for milestone 5 (topics, questions, quizzes, study plans).

Run from Backend: python tests/verify_learning_postgres.py
Applies all migrations in a fresh, transaction-scoped schema, compares the models with the
migrated schema, runs the practice loop through the API, and checks cascade/SET NULL rules,
constraints, downgrade and re-upgrade. Everything is rolled back; development data is unchanged.
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.security import create_access_token
from app.main import app
from app.models.chunk import DocumentChunk
from app.models.course import Course, Enrollment
from app.models.document import Document
from app.models.learning import QuizAttemptAnswer, QuizQuestion
from app.models.role import Role
from app.models.user import User
from database.connection import Base, engine, get_db

LEARNING_TABLES = {"topics", "quiz_questions", "quiz_attempts", "quiz_attempt_answers", "study_plan_settings", "study_sessions"}
QUOTE = "A subnet mask separates the network portion of an address from the host portion."


def schema_differences(connection, schema):
    context = MigrationContext.configure(connection, opts={"include_schemas": False, "compare_type": True})
    tables = {name for name in Base.metadata.tables if name in LEARNING_TABLES}
    differences = []
    for difference in compare_metadata(context, Base.metadata):
        flat = difference if isinstance(difference, tuple) else difference[0]
        if any(getattr(item, "name", None) in tables or getattr(getattr(item, "table", None), "name", None) in tables
               for item in flat if item is not None):
            differences.append(difference)
    return differences


async def practice_loop(connection):
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    app.dependency_overrides[get_db] = lambda: session
    try:
        student_role, lecturer_role = Role(name="STUDENT"), Role(name="LECTURER")
        session.add_all([student_role, lecturer_role])
        session.flush()
        student = User(full_name="Student", email="learner@example.com", hashed_password="fixture", role_id=student_role.id)
        lecturer = User(full_name="Lecturer", email="teacher@example.com", hashed_password="fixture", role_id=lecturer_role.id)
        session.add_all([student, lecturer])
        session.flush()
        course = Course(code="NET101", title="Networking", instructor_id=lecturer.id)
        session.add(course)
        session.flush()
        session.add(Enrollment(course_id=course.id, user_id=student.id))
        document = Document(owner_id=lecturer.id, course_id=course.id, visibility="course", original_filename="lecture.pdf",
                            stored_filename=f"{uuid.uuid4()}.pdf", file_type="pdf", file_size_bytes=10, processing_status="embedded")
        session.add(document)
        session.flush()
        chunk = DocumentChunk(document_id=document.id, chunk_index=0, content="Intro. " + QUOTE, source_location={"kind": "pdf_page", "page": 2})
        session.add(chunk)
        session.commit()
        teacher = {"Authorization": "Bearer " + create_access_token({"sub": str(lecturer.id)})}
        learner = {"Authorization": "Bearer " + create_access_token({"sub": str(student.id)})}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/courses/{course.id}/topics", headers=teacher, json={"name": "Subnetting"})
            assert response.status_code == 201, response.text
            topic_id = response.json()["id"]
            stems = ["What does a subnet mask separate?", "Which portion identifies the device?",
                     "Which portion is shared on a subnet?", "What is split by the mask?"]
            question_ids = []
            for stem in stems:
                response = await client.post("/questions", headers=teacher, json={
                    "topic_id": topic_id, "chunk_id": str(chunk.id), "source_quote": QUOTE, "stem": stem,
                    "options": ["Network and host portions", "Two routers", "Ports", "Frames"], "correct_index": 0,
                    "explanation": "The mask splits an address into network and host parts."})
                assert response.status_code == 201, response.text
                question_ids.append(response.json()["id"])
                response = await client.post(f"/questions/{question_ids[-1]}/publish", headers=teacher)
                assert response.status_code == 200 and response.json()["visibility"] == "course", response.text
            response = await client.put(f"/courses/{course.id}/study-plan/settings", headers=learner,
                                        json={"exam_date": None, "weekday_minutes": [30] * 7, "topic_difficulty": {topic_id: 3}})
            assert response.status_code == 200 and len(response.json()["sessions"]) == 7, response.text
            response = await client.post("/quizzes", headers=learner, json={"course_id": str(course.id), "size": 4})
            assert response.status_code == 201, response.text
            quiz = response.json()
            assert all(item["correct_index"] is None for item in quiz["items"])
            response = await client.post(f"/quizzes/{quiz['id']}/submit", headers=learner, json={"answers": {"0": 0, "1": 1, "2": 2, "3": 3}})
            assert response.status_code == 200 and response.json()["correct_count"] == 1, response.text
            response = await client.post(f"/quizzes/{quiz['id']}/submit", headers=learner, json={"answers": {}})
            assert response.status_code == 409, response.text
            plan = (await client.get(f"/courses/{course.id}/study-plan", headers=learner)).json()
            mastery = plan["mastery"][0]
            assert (mastery["level"], mastery["score"], mastery["answers"]) == ("weak", 0.25, 4), mastery
            assert all(s["kind"] == "revision" for s in plan["sessions"] if s["status"] == "planned"), plan["sessions"]
            assert "you marked it as difficult" in plan["sessions"][0]["reason"], plan["sessions"][0]
            response = await client.post(f"/study-sessions/{plan['sessions'][0]['id']}/complete", headers=learner)
            assert response.status_code == 200, response.text

            # Reprocessing replaces chunks: the FK clears, and the next quiz re-anchors by quote.
            session.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).delete()
            session.add(DocumentChunk(document_id=document.id, chunk_index=0, content=QUOTE, source_location={"kind": "pdf_page", "page": 5}))
            session.commit()
            assert session.get(QuizQuestion, uuid.UUID(question_ids[0])).chunk_id is None
            response = await client.post("/quizzes", headers=learner, json={"course_id": str(course.id), "size": 1})
            assert response.status_code == 201, response.text
            session.expire_all()
            assert session.get(QuizQuestion, uuid.UUID(question_ids[0])).source_location["page"] == 5

            # Removing the source document removes its questions and keeps graded history.
            session.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).delete()
            session.delete(session.get(Document, document.id))
            session.commit()
            assert session.query(QuizQuestion).count() == 0
            answers = session.query(QuizAttemptAnswer).filter(QuizAttemptAnswer.attempt_id == uuid.UUID(quiz["id"])).all()
            assert len(answers) == 4 and all(a.question_id is None for a in answers)
            response = await client.get(f"/quizzes/{quiz['id']}", headers=learner)
            assert response.json()["correct_count"] == 1 and all(i["source_unavailable"] for i in response.json()["items"])
        return course.id, lecturer.id, uuid.UUID(topic_id)
    finally:
        app.dependency_overrides.clear()
        session.close()


def check_constraints(connection, course_id, user_id, topic_id):
    violations = [
        "INSERT INTO study_sessions (id, user_id, course_id, topic_id, session_date, minutes, kind, reason) VALUES (:id, :user, :course, :topic, CURRENT_DATE, 0, 'quiz', 'x')",
        "INSERT INTO study_sessions (id, user_id, course_id, topic_id, session_date, minutes, kind, reason) VALUES (:id, :user, :course, :topic, CURRENT_DATE, 30, 'nap', 'x')",
        "INSERT INTO study_sessions (id, user_id, course_id, topic_id, session_date, minutes, kind, reason, status) VALUES (:id, :user, :course, :topic, CURRENT_DATE, 30, 'quiz', 'x', 'skipped')",
        "INSERT INTO topics (id, course_id, name) VALUES (:id, :course, 'Subnetting')",
        "INSERT INTO quiz_attempts (id, user_id, course_id, total_count, status) VALUES (:id, :user, :course, 1, 'abandoned')",
        "INSERT INTO study_plan_settings (id, user_id, course_id, weekday_minutes, topic_difficulty) SELECT :id, user_id, course_id, weekday_minutes, topic_difficulty FROM study_plan_settings LIMIT 1",
    ]
    for statement in violations:
        try:
            with connection.begin_nested():
                connection.execute(text(statement), {"id": uuid.uuid4(), "user": user_id, "course": course_id, "topic": topic_id})
        except IntegrityError:
            continue
        raise AssertionError(f"Database accepted an invalid row: {statement}")


def verify():
    schema = "learning_migration_test_" + uuid.uuid4().hex
    assert schema.isidentifier()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            config.attributes["version_table_schema"] = schema
            command.upgrade(config, "head")
            assert LEARNING_TABLES <= set(inspect(connection).get_table_names(schema=schema))
            differences = schema_differences(connection, schema)
            assert not differences, f"Models and migration differ: {differences}"
            print("Migration f5c8a2d91b37 applied; learning models match the migrated schema")
            course_id, user_id, topic_id = asyncio.run(practice_loop(connection))
            print("API practice loop on PostgreSQL: topics, publish, plan settings, quiz, grading, mastery, re-anchoring, cascades: passed")
            check_constraints(connection, course_id, user_id, topic_id)
            print("Check, unique and status constraints: passed")
            command.downgrade(config, "e7b2c3d4f501")
            assert not LEARNING_TABLES & set(inspect(connection).get_table_names(schema=schema))
            assert connection.scalar(text("SELECT count(*) FROM courses")) == 1
            command.upgrade(config, "head")
            assert LEARNING_TABLES <= set(inspect(connection).get_table_names(schema=schema))
            print("Downgrade to e7b2c3d4f501 keeps earlier data; re-upgrade succeeds")
        finally:
            transaction.rollback()
            print("Temporary schema rolled back; development records unchanged")


if __name__ == "__main__":
    verify()
