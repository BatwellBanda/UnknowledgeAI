"""Opt-in real PostgreSQL API/worker/recovery checks in an isolated schema.

Run from Backend: python tests/verify_processing_postgres.py
Embedding inference is stubbed; files, jobs, locks, transactions, and stages are real.
"""
import asyncio
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import Session
from app.main import app
from app.core.security import create_access_token
from app.models.role import Role
from app.models.user import User
from app.models.processing_job import ProcessingJob
from app.services.processing_service import run_next_job, document_lock
from database.connection import engine, get_db
from embedding_fixtures import embedding_model


async def verify(scoped_engine, folder):
    with Session(scoped_engine) as session:
        role = Role(name="STUDENT")
        session.add(role)
        session.flush()
        user = User(full_name="Worker fixture", email="worker@example.com", hashed_password="fixture", role_id=role.id)
        session.add(user)
        session.commit()
        user_id = user.id

    def database():
        with Session(scoped_engine) as session:
            yield session

    app.dependency_overrides[get_db] = database
    headers = {"Authorization": "Bearer " + create_access_token({"sub": str(user_id)})}
    model = embedding_model()
    with patch("app.services.document_service.UPLOAD_DIR", str(folder)), patch("app.services.extraction_service.UPLOAD_DIR", str(folder)), patch("app.services.embedding_service.get_model", return_value=model):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            uploaded = await client.post("/documents/upload", headers=headers, files={"file": ("fixture.txt", b"Document worker fixture text.")})
            assert uploaded.status_code == 200, uploaded.text
            document_id = uuid.UUID(uploaded.json()["id"])
            process_url = f"/documents/{document_id}/process"
            queued = await client.post(process_url, headers=headers)
            again = await client.post(process_url, headers=headers)
            assert queued.status_code == 202 and queued.json()["id"] == again.json()["id"]
            job_id = uuid.UUID(queued.json()["id"])
            with document_lock(scoped_engine, document_id) as held:
                assert held
                assert run_next_job(scoped_engine) is False
            assert run_next_job(scoped_engine) is True
            status = await client.get(f"/documents/{document_id}/processing", headers=headers)
            assert status.json()["status"] == "completed" and status.json()["attempts"] == 1
            chunks = await client.get(f"/documents/{document_id}/chunks", headers=headers)
            assert chunks.json()[0]["source_location"]["kind"] == "txt_lines"

            # Simulate a worker that died after claiming the next job.
            queued = await client.post(process_url, headers=headers)
            new_id = uuid.UUID(queued.json()["id"])
            with Session(scoped_engine) as session:
                job = session.get(ProcessingJob, new_id)
                job.status = "running"
                job.attempts = 1
                job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                session.commit()
            with document_lock(scoped_engine, document_id) as held:
                assert held
                assert run_next_job(scoped_engine) is False, "A live lock must block stale-lease takeover"
            assert run_next_job(scoped_engine) is True
            with Session(scoped_engine) as session:
                recovered = session.get(ProcessingJob, new_id)
                assert recovered.status == "completed" and recovered.attempts == 2

            # Bound automatic recovery; a user can explicitly request a fresh retry.
            queued = await client.post(process_url, headers=headers)
            interrupted_id = uuid.UUID(queued.json()["id"])
            with Session(scoped_engine) as session:
                job = session.get(ProcessingJob, interrupted_id)
                job.status = "running"
                job.attempts = 3
                job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                session.commit()
            assert run_next_job(scoped_engine) is True
            with Session(scoped_engine) as session:
                assert session.get(ProcessingJob, interrupted_id).status == "failed"
            assert run_next_job(scoped_engine) is False
            queued = await client.post(process_url, headers=headers)
            assert queued.status_code == 202 and queued.json()["id"] != str(interrupted_id)
            failed_id = uuid.UUID(queued.json()["id"])
            with patch.object(model, "encode", side_effect=RuntimeError("private/model/cache/path")):
                assert run_next_job(scoped_engine) is True
            with Session(scoped_engine) as session:
                failed = session.get(ProcessingJob, failed_id)
                assert failed.status == "failed" and failed.stage == "embed"
                assert "private/model/cache/path" not in failed.error_message
            queued = await client.post(process_url, headers=headers)
            assert queued.status_code == 202 and run_next_job(scoped_engine) is True
        print("PostgreSQL upload/queue/worker/stages/advisory exclusion/crash recovery/retry checks: passed")


if __name__ == "__main__":
    schema = "processing_test_" + uuid.uuid4().hex
    assert schema.isidentifier() and schema.startswith("processing_test_")
    scoped_engine = None
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped_engine = create_engine(engine.url, connect_args={"options": f"-csearch_path={schema},public"})
        with scoped_engine.begin() as connection:
            config = Config("alembic.ini")
            config.attributes.update(connection=connection, version_table_schema=schema)
            command.upgrade(config, "head")
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(verify(scoped_engine, Path(folder)))
        # Check downgrade/re-upgrade of the new queue table independently.
        with scoped_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "b81d732ac459")
            assert "processing_jobs" not in inspect(connection).get_table_names(schema=schema)
            command.upgrade(config, "head")
        print("Processing migration downgrade/re-upgrade: passed")
    finally:
        app.dependency_overrides.clear()
        if scoped_engine is not None:
            scoped_engine.dispose()
        # Only the fresh, UUID-named schema created above is removed.
        assert schema.isidentifier() and schema.startswith("processing_test_")
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        print("Temporary processing schema removed; development records unchanged")
