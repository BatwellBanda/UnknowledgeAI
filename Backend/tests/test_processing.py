import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from test_foundation import APITestCase
from embedding_fixtures import embedding_model
from app.models.processing_job import ProcessingJob
from app.models.chunk import DocumentChunk
from app.services import processing_service


class ProcessingAPITests(APITestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.upload_dir = Path(temporary.name)
        self.addCleanup(temporary.cleanup)
        (self.upload_dir / self.document.stored_filename).write_text("A document processing fixture.", encoding="utf-8")
        patcher = patch("app.services.extraction_service.UPLOAD_DIR", str(self.upload_dir))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def enqueue(self, user=None):
        return await self.client.post(f"/documents/{self.document.id}/process", headers=self.headers(user))

    def claim_for_stage_tests(self):
        job = self.db.query(ProcessingJob).one()
        job.status = "running"
        job.attempts = 1
        job.started_at = datetime.now(timezone.utc)
        self.db.commit()
        return job

    async def test_enqueue_and_duplicate_return_same_persisted_job(self):
        first = await self.enqueue()
        second = await self.enqueue()
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(first.json()["status"], "queued")
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(self.db.query(ProcessingJob).count(), 1)
        status = await self.client.get(f"/documents/{self.document.id}/processing", headers=self.headers())
        self.assertEqual(status.json()["id"], first.json()["id"])

    async def test_other_user_cannot_enqueue_or_inspect_job(self):
        response = await self.enqueue(self.other)
        self.assertEqual(response.status_code, 404)
        await self.enqueue()
        response = await self.client.get(f"/documents/{self.document.id}/processing", headers=self.headers(self.other))
        self.assertEqual(response.status_code, 404)

    async def test_job_endpoints_require_authentication(self):
        for method, suffix in [("POST", "process"), ("GET", "processing")]:
            response = await self.client.request(method, f"/documents/{self.document.id}/{suffix}")
            self.assertEqual(response.status_code, 401)

    async def test_status_without_job_is_404(self):
        response = await self.client.get(f"/documents/{self.document.id}/processing", headers=self.headers())
        self.assertEqual(response.status_code, 404)

    async def test_manual_steps_cannot_race_active_job(self):
        await self.enqueue()
        for operation in ["extract", "chunk", "embed"]:
            response = await self.client.post(f"/documents/{self.document.id}/{operation}", headers=self.headers())
            self.assertEqual(response.status_code, 409)

    async def test_pipeline_extracts_chunks_and_embeds_in_order(self):
        await self.enqueue()
        job = self.claim_for_stage_tests()
        model = embedding_model()
        with patch("app.services.embedding_service.get_model", return_value=model):
            processing_service.process_claimed_job(self.db, job)
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.stage, "done")
        self.assertEqual(self.document.processing_status, "embedded")
        chunk = self.db.query(DocumentChunk).one()
        self.assertEqual(chunk.source_location["kind"], "txt_lines")
        self.assertEqual(len(chunk.embedding), 384)

    async def test_failed_extraction_records_safe_error_and_allows_retry(self):
        await self.enqueue()
        job = self.claim_for_stage_tests()
        (self.upload_dir / self.document.stored_filename).write_bytes(b"")
        with self.assertLogs(level="ERROR"):
            processing_service.process_claimed_job(self.db, job)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.stage, "extract")
        self.assertEqual(self.document.processing_status, "failed")
        self.assertNotIn(str(self.upload_dir), job.error_message)
        retry = await self.enqueue()
        self.assertEqual(retry.status_code, 202)
        self.assertNotEqual(retry.json()["id"], str(job.id))
        status = await self.client.get(f"/documents/{self.document.id}/processing", headers=self.headers())
        self.assertEqual(status.json()["id"], retry.json()["id"])

    async def test_bad_embedding_does_not_mark_document_searchable(self):
        await self.enqueue()
        job = self.claim_for_stage_tests()
        model = embedding_model(zero_vectors=True)
        with patch("app.services.embedding_service.get_model", return_value=model):
            with self.assertLogs(level="ERROR"):
                processing_service.process_claimed_job(self.db, job)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.stage, "embed")
        self.assertEqual(self.document.processing_status, "failed")
