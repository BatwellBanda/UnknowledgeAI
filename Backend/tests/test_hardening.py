"""Regression tests for access control, abuse limits and upload safety."""
import io
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import select

from test_foundation import APITestCase, PASSWORD
from app.core import concurrency
from app.models.chunk import DocumentChunk
from app.models.course import Course, Enrollment
from app.models.document import Document
from app.models.role import Role
from app.models.user import User
from app.services import extraction_service, extractors


def office_file(marker: str, extra: dict | None = None) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(marker, "<document/>")
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return data.getvalue()


class HardeningTests(APITestCase):
    def setUp(self):
        super().setUp()
        lecturer_role = self.db.scalar(select(Role).where(Role.name == "LECTURER"))
        self.admin_role = self.db.scalar(select(Role).where(Role.name == "ADMIN"))
        self.lecturer = User(full_name="Lecturer", email="lecturer@example.com", hashed_password=self.fixture_hash, role_id=lecturer_role.id)
        self.admin = User(full_name="Admin", email="admin@example.com", hashed_password=self.fixture_hash, role_id=self.admin_role.id)
        self.db.add_all([self.lecturer, self.admin])
        self.db.flush()
        self.course = Course(code="SEC101", title="Security", instructor_id=self.lecturer.id)
        self.db.add(self.course)
        self.db.commit()
        temporary = tempfile.TemporaryDirectory()
        self.upload_dir = Path(temporary.name)
        self.addCleanup(temporary.cleanup)
        for target in ["app.services.document_service.UPLOAD_DIR", "app.services.extraction_service.UPLOAD_DIR"]:
            patcher = patch(target, str(self.upload_dir))
            patcher.start()
            self.addCleanup(patcher.stop)

    async def upload(self, filename="notes.txt", content=b"Readable notes.", user=None):
        return await self.client.post("/documents/upload", headers=self.headers(user), files={"file": (filename, content)})

    # Course access

    async def test_enrollment_requires_the_instructor_join_code(self):
        path = f"/courses/{self.course.id}/enroll"
        for body in [{}, {"join_code": "WRONG123"}]:
            response = await self.client.post(path, headers=self.headers(), json=body)
            self.assertIn(response.status_code, {403, 422})
        self.assertIsNone(self.db.scalar(select(Enrollment)))
        response = await self.client.post(path, headers=self.headers(), json={"join_code": f" {self.course.join_code.lower()} "})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_join_code_is_visible_only_to_course_managers(self):
        for user, visible in [(self.owner, False), (self.lecturer, True), (self.admin, True)]:
            listing = await self.client.get("/courses", headers=self.headers(user))
            detail = await self.client.get(f"/courses/{self.course.id}", headers=self.headers(user))
            for course in [listing.json()[0], detail.json()]:
                self.assertEqual(course["join_code"], self.course.join_code if visible else None)

    async def test_instructor_rotates_code_and_manages_roster(self):
        old_code = self.course.join_code
        denied = await self.client.post(f"/courses/{self.course.id}/join-code", headers=self.headers())
        self.assertEqual(denied.status_code, 403)
        rotated = await self.client.post(f"/courses/{self.course.id}/join-code", headers=self.headers(self.lecturer))
        self.assertEqual(rotated.status_code, 200)
        self.assertNotEqual(rotated.json()["join_code"], old_code)
        stale = await self.client.post(f"/courses/{self.course.id}/enroll", headers=self.headers(), json={"join_code": old_code})
        self.assertEqual(stale.status_code, 403)

        self.db.add(Enrollment(course_id=self.course.id, user_id=self.owner.id))
        self.db.commit()
        self.assertEqual((await self.client.get(f"/courses/{self.course.id}/students", headers=self.headers())).status_code, 403)
        roster = await self.client.get(f"/courses/{self.course.id}/students", headers=self.headers(self.lecturer))
        self.assertEqual([s["email"] for s in roster.json()], [self.owner.email])
        removed = await self.client.delete(f"/courses/{self.course.id}/students/{self.owner.id}", headers=self.headers(self.lecturer))
        self.assertEqual(removed.status_code, 204)
        self.assertIsNone(self.db.scalar(select(Enrollment)))

    # Sessions and accounts

    async def login(self, email=None, password=PASSWORD):
        return await self.client.post("/auth/login", json={"email": email or self.owner.email, "password": password})

    async def test_logout_revokes_every_issued_token(self):
        tokens = [(await self.login()).json()["access_token"] for _ in range(2)]
        headers = [{"Authorization": f"Bearer {token}"} for token in tokens]
        self.assertEqual((await self.client.post("/auth/logout", headers=headers[0])).status_code, 204)
        for header in headers:
            self.assertEqual((await self.client.get("/users/me", headers=header)).status_code, 401)
        fresh = (await self.login()).json()["access_token"]
        self.assertEqual((await self.client.get("/users/me", headers={"Authorization": f"Bearer {fresh}"})).status_code, 200)

    async def test_password_change_requires_current_password_and_revokes_old_tokens(self):
        old = {"Authorization": f"Bearer {(await self.login()).json()['access_token']}"}
        wrong = await self.client.post("/auth/change-password", headers=old, json={"current_password": "wrong", "new_password": "another long passphrase"})
        self.assertEqual(wrong.status_code, 400)
        changed = await self.client.post("/auth/change-password", headers=old, json={"current_password": PASSWORD, "new_password": "another long passphrase"})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual((await self.client.get("/users/me", headers=old)).status_code, 401)
        new = {"Authorization": f"Bearer {changed.json()['access_token']}"}
        self.assertEqual((await self.client.get("/users/me", headers=new)).status_code, 200)
        self.assertEqual((await self.login()).status_code, 401)
        self.assertEqual((await self.login(password="another long passphrase")).status_code, 200)

    async def test_admin_changes_roles_and_others_cannot(self):
        path = f"/users/{self.owner.id}/role"
        self.assertEqual((await self.client.patch(path, headers=self.headers(self.lecturer), json={"role": "ADMIN"})).status_code, 403)
        promoted = await self.client.patch(path, headers=self.headers(self.admin), json={"role": "lecturer"})
        self.assertEqual(promoted.status_code, 200, promoted.text)
        self.assertEqual(promoted.json()["role"], "LECTURER")
        own = await self.client.patch(f"/users/{self.admin.id}/role", headers=self.headers(self.admin), json={"role": "STUDENT"})
        self.assertEqual(own.status_code, 400)

    # Abuse limits

    async def test_repeated_login_failures_for_one_account_are_throttled(self):
        statuses = [(await self.login(password="wrong guess")).status_code for _ in range(11)]
        self.assertEqual(statuses[:10], [401] * 10)
        self.assertEqual(statuses[10], 429)
        throttled = await self.login()
        self.assertEqual(throttled.status_code, 429)
        self.assertIn("retry-after", throttled.headers)
        # Another account from the same network is unaffected.
        self.assertEqual((await self.login(self.other.email)).status_code, 200)

    async def test_chat_is_rate_limited_per_user(self):
        with patch("app.services.chat_service.answer", return_value={"status": "not_found", "answer": "", "citations": []}):
            statuses = [(await self.client.post("/chat", headers=self.headers(), json={"query": "q"})).status_code for _ in range(11)]
            self.assertEqual(statuses[-1], 429)
            other = await self.client.post("/chat", headers=self.headers(self.other), json={"query": "q"})
            self.assertEqual(other.status_code, 200)

    def test_busy_model_slots_return_503_instead_of_queueing_forever(self):
        semaphore = threading.BoundedSemaphore(1)
        semaphore.acquire()
        with patch.object(concurrency.settings, "BUSY_WAIT_SECONDS", 0.01):
            with self.assertRaises(Exception) as caught:
                with concurrency.slot(semaphore, "busy"):
                    pass
        self.assertEqual(caught.exception.status_code, 503)

    # Upload safety and storage

    async def test_office_files_must_be_real_bounded_archives(self):
        valid = await self.upload("lecture.docx", office_file("word/document.xml"))
        self.assertEqual(valid.status_code, 200, valid.text)
        for filename, content in [
            ("slides.pptx", office_file("word/document.xml")),
            ("lecture.docx", office_file("word/document.xml", {"bomb.xml": "0" * (25 * 1024 * 1024)})),
            ("lecture.pdf", b"MZ executable"),
            ("notes.txt", b"text\x00binary"),
        ]:
            response = await self.upload(filename, content)
            self.assertEqual(response.status_code, 400, (filename, response.text))
        self.assertEqual(len(list(self.upload_dir.iterdir())), 1)

    async def test_storage_quota_is_enforced(self):
        with patch.object(extraction_service.settings, "MAX_USER_STORAGE_MB", 1):
            self.document.file_size_bytes = 1024 * 1024 - 5
            self.db.commit()
            response = await self.upload(content=b"more than five bytes")
            self.assertEqual(response.status_code, 413)
            self.assertEqual(list(self.upload_dir.iterdir()), [])

    async def test_owner_deletes_document_chunks_and_file(self):
        uploaded = await self.upload()
        document_id = uploaded.json()["id"]
        self.db.add(DocumentChunk(document_id=uuid.UUID(document_id), chunk_index=0, content="Readable notes."))
        self.db.commit()
        self.assertEqual((await self.client.delete(f"/documents/{document_id}", headers=self.headers(self.other))).status_code, 404)
        response = await self.client.delete(f"/documents/{document_id}", headers=self.headers())
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.db.query(DocumentChunk).count(), 0)
        self.assertEqual(self.db.query(Document).filter(Document.id != self.document.id).count(), 0)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_extraction_runs_in_an_isolated_process(self):
        path = self.upload_dir / "isolated.txt"
        path.write_text("Isolated text", encoding="utf-8")
        with patch.object(extraction_service.settings, "EXTRACTION_TIMEOUT_SECONDS", 60):
            segments = extraction_service.run_extractor("txt", str(path))
        self.assertEqual(segments[0]["text"], "Isolated text")

    def test_hung_extraction_is_terminated(self):
        with patch.object(extraction_service.settings, "EXTRACTION_TIMEOUT_SECONDS", 0.5), \
                patch.dict(extractors.EXTRACTORS_DEFAULTS, {"txt": extractors.EXTRACTORS["txt"]}), \
                patch("app.services.extraction_service.extractors.extract", _sleep_forever):
            with self.assertRaises(extraction_service.ExtractionTimeout):
                extraction_service.run_extractor("txt", "unused.txt")


def _sleep_forever(file_type, file_path):
    import time
    time.sleep(60)
