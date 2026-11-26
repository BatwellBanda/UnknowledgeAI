"""Opt-in PostgreSQL check for lecture recordings (milestone 6).

Run from Backend: python tests/verify_recordings_postgres.py
Applies all migrations in a fresh, transaction-scoped schema and compares the models with it.
Then, through the API with real FFprobe and the cached embedding model, it:
- uploads a recording with captions and processes it;
- searches it and checks the timed source;
- streams a byte range; edits the transcript and writes notes;
- checks cascades and constraints, then downgrade and re-upgrade.
Everything is rolled back and the temporary upload folder is removed.
"""
import asyncio
import io
import math
import struct
import sys
import tempfile
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

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
from app.models.course import Course, Enrollment
from app.models.document import Document
from app.models.recording import Note, TranscriptSegment
from app.models.role import Role
from app.models.user import User
from database.connection import Base, engine, get_db

RECORDING_TABLES = {"transcript_segments", "notes"}
DOCUMENT_COLUMNS = {"media_kind", "media_duration_seconds", "transcript_source", "transcript_revision", "indexed_transcript_revision"}
CAPTIONS = """WEBVTT

00:00:00.500 --> 00:00:01.500
A subnet mask separates the network portion of an address from the host portion.

00:00:01.500 --> 00:00:02.500
A router forwards packets between networks using its routing table.
"""


def tone(seconds=3.0, rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 330 * i / rate))) for i in range(int(seconds * rate))))
    return buffer.getvalue()


def schema_differences(connection):
    context = MigrationContext.configure(connection, opts={"include_schemas": False, "compare_type": True})
    differences = []
    for difference in compare_metadata(context, Base.metadata):
        flat = difference if isinstance(difference, tuple) else difference[0]
        names = {getattr(item, "name", None) for item in flat if item is not None}
        names |= {getattr(getattr(item, "table", None), "name", None) for item in flat if item is not None}
        if names & RECORDING_TABLES or (names & {"documents"} and names & DOCUMENT_COLUMNS):
            differences.append(difference)
    return differences


async def recording_loop(connection, upload_dir):
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    app.dependency_overrides[get_db] = lambda: session
    try:
        roles = {name: Role(name=name) for name in ["STUDENT", "LECTURER"]}
        session.add_all(roles.values())
        session.flush()
        student = User(full_name="Student", email="learner@example.com", hashed_password="fixture", role_id=roles["STUDENT"].id)
        lecturer = User(full_name="Lecturer", email="teacher@example.com", hashed_password="fixture", role_id=roles["LECTURER"].id)
        session.add_all([student, lecturer])
        session.flush()
        course = Course(code="NET101", title="Networking", instructor_id=lecturer.id)
        session.add(course)
        session.flush()
        session.add(Enrollment(course_id=course.id, user_id=student.id))
        session.commit()
        teacher = {"Authorization": "Bearer " + create_access_token({"sub": str(lecturer.id)})}
        learner = {"Authorization": "Bearer " + create_access_token({"sub": str(student.id)})}
        audio = tone()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/documents/upload", headers=teacher, data={"course_id": str(course.id), "visibility": "course"},
                                         files={"file": ("week3.wav", audio), "captions": ("week3.vtt", CAPTIONS.encode())})
            assert response.status_code == 200, response.text
            document = response.json()
            assert document["media_kind"] == "audio" and abs(document["media_duration_seconds"] - 3.0) < 0.05, document
            for step in ["extract", "chunk", "embed"]:
                response = await client.post(f"/documents/{document['id']}/{step}", headers=teacher)
                assert response.status_code == 200, (step, response.text)
            response = await client.post("/search", headers=learner, json={"query": "What separates network and host bits?", "course_id": str(course.id)})
            assert response.status_code == 200, response.text
            location = response.json()["results"][0]["source_location"]
            assert (location["kind"], location["start_seconds"], location["end_seconds"]) == ("recording", 0.5, 2.5), location

            link = (await client.post(f"/documents/{document['id']}/media-link", headers=learner)).json()
            response = await client.get(link["url"], headers={"Range": "bytes=44-1043"})
            assert response.status_code == 206 and response.content == audio[44:1044], response.status_code

            transcript = (await client.get(f"/documents/{document['id']}/transcript", headers=teacher)).json()
            segment = transcript["segments"][1]["id"]
            response = await client.patch(f"/documents/{document['id']}/transcript/segments/{segment}", headers=teacher,
                                          json={"text": "A router forwards packets using its routing table."})
            assert response.status_code == 200 and response.json()["stale"], response.text
            response = await client.post(f"/documents/{document['id']}/notes", headers=learner, json={"body": "Mask vs host bits", "timestamp_seconds": 1.0})
            assert response.status_code == 201, response.text
            assert session.query(TranscriptSegment).filter(TranscriptSegment.document_id == uuid.UUID(document["id"])).count() == 2
            response = await client.delete(f"/documents/{document['id']}", headers=teacher)
            assert response.status_code == 204, response.text
        assert session.query(TranscriptSegment).count() == 0 and session.query(Note).count() == 0
        assert not any(Path(upload_dir).iterdir())
        return lecturer.id, course.id
    finally:
        app.dependency_overrides.clear()
        session.close()


def check_constraints(connection, user_id, course_id):
    document_id = uuid.uuid4()
    connection.execute(text(
        "INSERT INTO documents (id, owner_id, course_id, visibility, original_filename, stored_filename, file_type, file_size_bytes, processing_status) "
        "VALUES (:id, :user, :course, 'private', 'a.wav', :stored, 'wav', 1, 'uploaded')"), {"id": document_id, "user": user_id, "course": course_id, "stored": f"{uuid.uuid4()}.wav"})
    connection.execute(text("INSERT INTO transcript_segments (id, document_id, position, start_seconds, end_seconds, text) VALUES (:id, :doc, 0, 0, 1, 'x')"),
                       {"id": uuid.uuid4(), "doc": document_id})
    violations = [
        "UPDATE documents SET media_kind = 'image' WHERE id = :doc",
        "UPDATE documents SET media_duration_seconds = -1 WHERE id = :doc",
        "INSERT INTO transcript_segments (id, document_id, position, start_seconds, end_seconds, text) VALUES (:id, :doc, 0, 2, 3, 'duplicate position')",
        "INSERT INTO transcript_segments (id, document_id, position, start_seconds, end_seconds, text) VALUES (:id, :doc, 1, 5, 4, 'backwards')",
        "INSERT INTO transcript_segments (id, document_id, position, start_seconds, end_seconds, text) VALUES (:id, :doc, -1, 0, 1, 'negative')",
        "INSERT INTO notes (id, owner_id, document_id, timestamp_seconds, body) VALUES (:id, :user, :doc, -5, 'before start')",
    ]
    for statement in violations:
        try:
            with connection.begin_nested():
                connection.execute(text(statement), {"id": uuid.uuid4(), "doc": document_id, "user": user_id})
        except IntegrityError:
            continue
        raise AssertionError(f"Database accepted an invalid row: {statement}")


def verify():
    schema = "recording_migration_test_" + uuid.uuid4().hex
    assert schema.isidentifier()
    with tempfile.TemporaryDirectory() as folder, engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            config.attributes["version_table_schema"] = schema
            command.upgrade(config, "head")
            assert RECORDING_TABLES <= set(inspect(connection).get_table_names(schema=schema))
            differences = schema_differences(connection)
            assert not differences, f"Models and migration differ: {differences}"
            print("Migration a3d9e6f1c204 applied; recording models match the migrated schema")
            with patch("app.services.document_service.UPLOAD_DIR", folder), patch("app.services.extraction_service.UPLOAD_DIR", folder):
                user_id, course_id = asyncio.run(recording_loop(connection, folder))
            print("API loop on PostgreSQL: upload with captions, real FFprobe, processing, search with timed source, "
                  "byte-range playback, transcript edit, notes, cascades: passed")
            check_constraints(connection, user_id, course_id)
            print("Media, transcript and note constraints: passed")
            command.downgrade(config, "f5c8a2d91b37")
            assert not RECORDING_TABLES & set(inspect(connection).get_table_names(schema=schema))
            assert not DOCUMENT_COLUMNS & {c["name"] for c in inspect(connection).get_columns("documents", schema=schema)}
            assert connection.scalar(text("SELECT count(*) FROM documents")) == 1
            command.upgrade(config, "head")
            assert RECORDING_TABLES <= set(inspect(connection).get_table_names(schema=schema))
            print("Downgrade to f5c8a2d91b37 keeps existing documents; re-upgrade succeeds")
        finally:
            transaction.rollback()
            print("Temporary schema rolled back; development records unchanged")


if __name__ == "__main__":
    verify()
