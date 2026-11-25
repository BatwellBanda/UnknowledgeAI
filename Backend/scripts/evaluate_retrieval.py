"""Actual upload/extract/chunk/embed/search baseline in a rolled-back PG schema."""
import argparse
import asyncio
import hashlib
from importlib.metadata import version
import json
import os
import platform
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from evaluation.retrieval import Dataset, first_relevant_rank, summarise


async def evaluate(connection, dataset, folder):
    import httpx
    from sqlalchemy.orm import Session
    from app.main import app
    from app.core.security import create_access_token
    from app.models.user import User
    from app.models.role import Role
    from app.models.course import Course, Enrollment
    from app.services.embedding_service import get_model
    from app.services.search_service import query_embedding
    from app.services.chunking_service import CHUNK_SIZE, CHUNK_OVERLAP
    from database.connection import get_db

    previous_overrides = app.dependency_overrides.copy()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    app.dependency_overrides[get_db] = lambda: session
    try:
        role = Role(name="STUDENT")
        session.add(role)
        session.flush()
        user = User(full_name="Evaluation fixture", email="evaluation@example.com", hashed_password="unused", role_id=role.id)
        session.add(user)
        session.flush()
        courses = {}
        for index, key in enumerate(sorted({m.course for m in dataset.materials})):
            course = Course(code=f"EVAL{index:03d}", title=key, instructor_id=user.id)
            session.add(course)
            session.flush()
            session.add(Enrollment(course_id=course.id, user_id=user.id))
            courses[key] = str(course.id)
        session.commit()
        headers = {"Authorization": "Bearer " + create_access_token({"sub": str(user.id)})}
        document_ids = {}
        chunk_count = 0
        with patch("app.services.document_service.UPLOAD_DIR", str(folder)), patch("app.services.extraction_service.UPLOAD_DIR", str(folder)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://evaluation") as client:
                async def post(path, **kwargs):
                    response = await client.post(path, headers=headers, **kwargs)
                    if response.status_code != 200:
                        raise RuntimeError(f"Evaluation request failed: {path} ({response.status_code}): {response.text}")
                    return response.json()

                for material in dataset.materials:
                    uploaded = await post("/documents/upload", files={"file": (material.id + ".txt", material.text.encode("utf-8"))},
                                          data={"course_id": courses[material.course]})
                    document_id = uploaded["id"]
                    document_ids[material.id] = document_id
                    await post(f"/documents/{document_id}/extract")
                    chunks = await post(f"/documents/{document_id}/chunk")
                    await post(f"/documents/{document_id}/embed")
                    chunk_count += len(chunks)
                query_embedding("Warm up the retrieval model")
                rows = []
                for question in dataset.questions:
                    payload = {"query": question.query, "top_k": 5}
                    if question.course is not None:
                        payload["course_id"] = courses[question.course]
                    started = time.perf_counter()
                    response = await post("/search", json=payload)
                    latency_ms = (time.perf_counter() - started) * 1000
                    rows.append({"id": question.id, "query": question.query, "course": question.course,
                                 "kind": question.kind, "latency_ms": latency_ms,
                                 "first_relevant_rank": first_relevant_rank(question, response["results"], document_ids),
                                 "results": [{"document": next(key for key, value in document_ids.items() if value == result["document_id"]),
                                              "chunk_index": result["chunk_index"], "similarity": result["similarity"],
                                              "source_location": result["source_location"], "text": result["text"]}
                                             for result in response["results"]]})
        model = get_model()
        return {"dataset_version": dataset.version, "split": dataset.split, "description": dataset.description,
                "materials": len(dataset.materials), "chunks": chunk_count,
                "model": {"name": "all-MiniLM-L6-v2", "max_tokens": model.max_seq_length,
                          "revision": getattr(model[0].auto_model.config, "_commit_hash", None), "device": str(model.device)},
                "chunking": {"max_characters": CHUNK_SIZE, "max_overlap_characters": CHUNK_OVERLAP},
                "environment": {"python": platform.python_version(), "platform": platform.platform(),
                                "processor": platform.processor(), "logical_cpus": os.cpu_count(),
                                "packages": {name: version(name) for name in ["sentence-transformers", "transformers", "torch", "pgvector", "SQLAlchemy"]}},
                "metrics": summarise(rows), "questions": rows,
                "limitations": ["Single warmed run via in-process authenticated API; excludes network and cold-start latency.",
                                "A hit requires any labelled document and evidence passage; it is not full multi-source recall.",
                                "Unanswerable probes are recorded but not scored as abstentions; retrieval has no default cutoff.",
                                "No generation, faithfulness, usability, or learning-outcome score is measured."]}
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=BACKEND / "evaluation/datasets/starter.json")
    parser.add_argument("--output", type=Path, default=BACKEND.parent / "tmp/evaluation/retrieval-baseline.json")
    args = parser.parse_args()
    raw = args.dataset.read_bytes()
    dataset = Dataset.model_validate_json(raw)
    # Keep evaluation repeatable and avoid unexpected runtime downloads.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from database.connection import engine
    schema = "retrieval_eval_" + uuid.uuid4().hex
    assert schema.isidentifier() and schema.startswith("retrieval_eval_")
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            config = Config(str(BACKEND / "alembic.ini"))
            config.set_main_option("script_location", str(BACKEND / "alembic"))
            config.attributes.update(connection=connection, version_table_schema=schema)
            command.upgrade(config, "head")
            with tempfile.TemporaryDirectory() as folder:
                report = asyncio.run(evaluate(connection, dataset, Path(folder)))
        finally:
            transaction.rollback()
    report["dataset_sha256"] = hashlib.sha256(raw).hexdigest()
    report["implementation_sha256"] = {
        name: hashlib.sha256((BACKEND / name).read_bytes()).hexdigest()
        for name in ["app/services/chunking_service.py", "app/services/embedding_service.py",
                     "app/services/extraction_service.py", "app/services/search_service.py",
                     "evaluation/retrieval.py", "scripts/evaluate_retrieval.py"]
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2))
    print(f"Report: {args.output.resolve()}")
    print("Temporary evaluation schema rolled back; development records unchanged.")


if __name__ == "__main__":
    main()
