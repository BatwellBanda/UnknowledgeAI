"""Opt-in API/pgvector check with deterministic vectors and a rolled-back schema.

Run from Backend: python tests/verify_search_postgres.py
The model is stubbed for ranking tests; PostgreSQL cosine queries and permissions are real.
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config
from app.main import app
from app.core.security import create_access_token
from app.models.user import User
from app.models.role import Role
from app.models.course import Course, Enrollment
from app.models.document import Document
from app.models.chunk import DocumentChunk
from database.connection import engine, get_db


async def verify(connection):
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    app.dependency_overrides[get_db] = lambda: session
    checks = 0
    try:
        role = Role(name="STUDENT")
        session.add(role)
        session.flush()
        student = User(full_name="Student", email="search@example.com", hashed_password="fixture", role_id=role.id)
        instructor = User(full_name="Instructor", email="instructor@example.com", hashed_password="fixture", role_id=role.id)
        outsider = User(full_name="Outsider", email="outsider@example.com", hashed_password="fixture", role_id=role.id)
        session.add_all([student, instructor, outsider])
        session.flush()
        course = Course(code="SEARCH101", title="Search course", instructor_id=instructor.id)
        session.add(course)
        session.flush()
        enrollment = Enrollment(course_id=course.id, user_id=student.id)
        session.add(enrollment)
        documents = {}

        def add(name, owner, vector, *, visibility="private", course_id=None, state="embedded", source=True, content=None):
            document = Document(owner_id=owner.id, course_id=course_id, visibility=visibility, original_filename=name + ".txt", stored_filename=name + ".txt", file_type="txt", file_size_bytes=10, processing_status=state)
            session.add(document)
            session.flush()
            session.add(DocumentChunk(document_id=document.id, chunk_index=0, content=content or name, embedding=vector, source_location={"kind": "txt_lines", "line_start": 1, "line_end": 1} if source else None))
            documents[name] = document

        aligned = [1.0] + [0.0] * 383
        partial = [0.8, 0.6] + [0.0] * 382
        opposite = [-1.0] + [0.0] * 383
        add("owned", student, partial)
        add("legacy", student, opposite, source=False)
        add("published", instructor, aligned, visibility="course", course_id=course.id)
        add("private-student", outsider, aligned, course_id=course.id)
        add("private-instructor", instructor, aligned, course_id=course.id)
        add("failed", student, aligned, state="failed")
        add("not-ready", student, aligned, state="chunked")
        add("null-vector", student, None)
        add("zero-vector", student, [0.0] * 384)
        session.commit()
        headers = {"Authorization": "Bearer " + create_access_token({"sub": str(student.id)})}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def search(**changes):
                return await client.post("/search", headers=headers, json={"query": "Question", **changes})

            with patch("app.services.search_service.query_embedding", return_value=aligned):
                response = await search(top_k=20)
                assert response.status_code == 200, response.text
                results = response.json()["results"]
                assert [r["text"] for r in results] == ["published", "owned", "legacy"]
                assert abs(results[0]["similarity"] - 1) < 1e-6
                assert abs(results[1]["similarity"] - 0.8) < 1e-6
                assert results[0]["source_location"]["line_start"] == 1
                assert results[-1]["source_location"] is None
                checks += 1
                response = await search(top_k=1)
                assert len(response.json()["results"]) == 1 and response.json()["results"][0]["text"] == "published"
                checks += 1
                response = await search(min_similarity=0.9)
                assert [r["text"] for r in response.json()["results"]] == ["published"]
                checks += 1
                response = await search(document_id=str(documents["legacy"].id), min_similarity=0)
                assert response.json()["status"] == "no_matches" and response.json()["results"] == []
                checks += 1
                response = await search(course_id=str(course.id))
                assert [r["text"] for r in response.json()["results"]] == ["published"]
                checks += 1
                for name in ["private-student", "private-instructor", "failed", "not-ready", "null-vector", "zero-vector"]:
                    response = await search(document_id=str(documents[name].id))
                    assert response.json()["status"] == "no_searchable_materials", response.text
                    checks += 1
                session.delete(enrollment)
                session.commit()
                response = await search(course_id=str(course.id))
                assert response.json()["status"] == "no_searchable_materials"
                response = await search()
                assert {r["text"] for r in response.json()["results"]} == {"owned", "legacy"}
                checks += 1
                # Chat uses the real retrieval SQL and validates provider output against it.
                regenerated_enrollment = Enrollment(course_id=course.id, user_id=student.id)
                session.add(regenerated_enrollment)
                session.commit()
                generated = {"status": "answered", "claims": [{"text": "Published material.", "evidence": [{"source_id": "S1", "quote": "published"}]}]}
                with patch("app.services.llm_service.generate_answer", return_value=generated):
                    response = await client.post("/chat", headers=headers, json={"query": "Question", "course_id": str(course.id)})
                assert response.status_code == 200, response.text
                assert response.json()["citations"][0]["document_id"] == str(documents["published"].id)
                with patch("app.services.llm_service.generate_answer") as generate:
                    response = await client.post("/chat", headers=headers, json={"query": "Question", "document_id": str(documents["private-student"].id)})
                assert response.json()["status"] == "no_searchable_materials"
                generate.assert_not_called()

                def revoke(*args):
                    session.delete(regenerated_enrollment)
                    session.commit()
                    return generated

                with patch("app.services.llm_service.generate_answer", side_effect=revoke):
                    response = await client.post("/chat", headers=headers, json={"query": "Question", "course_id": str(course.id)})
                assert response.status_code == 409, response.text
                print("PostgreSQL chat retrieval/citation/private-scope/revocation checks: passed (generation stubbed)")
            if "--ollama" in sys.argv:
                passage = "A subnet mask separates a network address from its host portion."
                add("ollama-fixture", student, aligned, content=passage)
                session.commit()
                scope = {"document_id": str(documents["ollama-fixture"].id)}
                with patch("app.services.search_service.query_embedding", return_value=aligned):
                    response = await client.post("/chat", headers=headers, json={"query": "What does a subnet mask do?", **scope})
                    assert response.status_code == 200, response.text
                    assert response.json()["status"] == "answered" and response.json()["citations"], response.text
                    response = await client.post("/chat", headers=headers, json={"query": "When was this university founded?", **scope})
                    assert response.status_code == 200 and response.json()["status"] == "not_found", response.text
                print("Actual Ollama -> API -> PostgreSQL retrieval -> verified answer and abstention: passed (query vector fixed)")
            if "--real-model" in sys.argv:
                # Uses existing model cache and never downloads during this verification.
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
                from app.services.embedding_service import get_model

                passages = [
                    "A subnet mask separates a network address from its host portion.",
                    "Photosynthesis converts sunlight into chemical energy.",
                ]
                vectors = get_model().encode(passages, show_progress_bar=False)
                for name, passage, vector in zip(["real-network", "real-biology"], passages, vectors):
                    add(name, student, vector, course_id=course.id, content=passage)
                session.commit()
                response = await client.post("/search", headers=headers, json={"query": "What does a subnet mask do?", "course_id": str(course.id)})
                assert response.status_code == 200, response.text
                assert response.json()["results"][0]["text"] == passages[0], response.text
                print("Actual cached model -> authenticated API -> PostgreSQL cosine search -> source result: passed")

                # A character-only window loses the ending of this token-dense
                # source. Exercise chunking and embedding through their real APIs.
                from app.services.chunking_service import token_chunk_spans
                model = get_model()
                dense = "x = 1; " * 160
                assert len(model.tokenizer.encode(dense[:1000], add_special_tokens=True, truncation=False)) > model.max_seq_length
                for original in [dense, "\u7f51\u7edc\u5730\u5740" * 350, "uninterrupted" * 300, "\n  " * 400 + dense]:
                    covered = set()
                    for start, end in token_chunk_spans(original, model.tokenizer, model.max_seq_length):
                        assert len(model.tokenizer.encode(original[start:end].strip(), add_special_tokens=True, truncation=False)) <= model.max_seq_length
                        covered.update(range(start, end))
                    assert covered == set(range(len(original)))
                ending = "A subnet mask separates a network address from its host portion."
                document = Document(owner_id=student.id, original_filename="dense-source.txt", stored_filename="dense-source.txt",
                                    file_type="txt", file_size_bytes=len(dense + ending), processing_status="extracted",
                                    extracted_text=dense + "\n" + ending,
                                    extracted_segments=[{"text": dense, "location": {"kind": "pdf_page", "page": 4}},
                                                        {"text": ending, "location": {"kind": "pdf_page", "page": 7}}])
                session.add(document)
                session.commit()
                document_id = str(document.id)
                response = await client.post(f"/documents/{document_id}/chunk", headers=headers)
                assert response.status_code == 200, response.text
                chunks = response.json()
                assert chunks[-1]["content"] == ending and chunks[-1]["source_location"]["page"] == 7
                assert all(len(model.tokenizer.encode(c["content"], add_special_tokens=True, truncation=False)) <= model.max_seq_length for c in chunks)
                response = await client.post(f"/documents/{document_id}/embed", headers=headers)
                assert response.status_code == 200, response.text
                response = await client.post("/search", headers=headers,
                                             json={"query": "What does a subnet mask do?", "document_id": document_id})
                assert response.status_code == 200, response.text
                assert response.json()["results"][0]["text"] == ending, response.text
                print(f"Real tokenizer coverage -> {len(chunks)} bounded source chunks -> real embeddings -> PostgreSQL source retrieval: passed (limit {model.max_seq_length} tokens)")
        print(f"PostgreSQL API search: {checks} ranking/filter/privacy/revocation cases passed")
    finally:
        app.dependency_overrides.clear()
        session.close()


if __name__ == "__main__":
    schema = "search_test_" + uuid.uuid4().hex
    assert schema.isidentifier() and schema.startswith("search_test_")
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            config = Config("alembic.ini")
            config.attributes.update(connection=connection, version_table_schema=schema)
            command.upgrade(config, "head")
            asyncio.run(verify(connection))
        finally:
            transaction.rollback()
            print("Temporary search schema rolled back; development data unchanged")
