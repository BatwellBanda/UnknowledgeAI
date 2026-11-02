import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from test_foundation import APITestCase
from app.models.course import Course, Enrollment


class DownloadTests(APITestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.upload_dir = Path(temporary.name) / "uploads"
        self.upload_dir.mkdir()
        patcher = patch("app.services.document_service.UPLOAD_DIR", str(self.upload_dir))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.content = b"Original material\r\nwith unchanged bytes\x00\xff"
        (self.upload_dir / self.document.stored_filename).write_bytes(self.content)
        self.endpoint = f"/documents/{self.document.id}/download"

    async def test_owner_receives_original_bytes_as_private_attachment(self):
        response = await self.client.get(self.endpoint, headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.content)
        self.assertEqual(response.headers["content-type"], "application/octet-stream")
        self.assertEqual(response.headers["content-disposition"], 'attachment; filename="private.txt"')
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    async def test_authentication_and_private_ownership_are_required(self):
        self.assertEqual((await self.client.get(self.endpoint)).status_code, 401)
        denied = await self.client.get(self.endpoint, headers=self.headers(self.other))
        missing = await self.client.get(f"/documents/{uuid.uuid4()}/download", headers=self.headers(self.other))
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.json(), missing.json())

    async def test_course_access_is_rechecked_after_enrolment_revocation(self):
        course = Course(code="DOWNLOAD101", title="Download fixture", instructor_id=self.owner.id)
        self.db.add(course)
        self.db.flush()
        self.document.course_id = course.id
        self.document.visibility = "course"
        enrollment = Enrollment(course_id=course.id, user_id=self.other.id)
        self.db.add(enrollment)
        self.db.commit()
        response = await self.client.get(self.endpoint, headers=self.headers(self.other))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.content)
        self.db.delete(enrollment)
        self.db.commit()
        response = await self.client.get(self.endpoint, headers=self.headers(self.other))
        self.assertEqual(response.status_code, 404)

    async def test_missing_file_has_controlled_response(self):
        (self.upload_dir / self.document.stored_filename).unlink()
        response = await self.client.get(self.endpoint, headers=self.headers())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Original file unavailable"})
        self.assertNotIn(str(self.upload_dir), response.text)

    async def test_corrupt_stored_paths_cannot_escape_upload_directory(self):
        outside = self.upload_dir.parent / "outside.txt"
        outside.write_text("Do not expose this file")
        for stored in ["../outside.txt", str(outside), "."]:
            self.document.stored_filename = stored
            self.db.commit()
            response = await self.client.get(self.endpoint, headers=self.headers())
            self.assertEqual(response.status_code, 404)
            self.assertNotIn("Do not expose", response.text)

    async def test_attachment_filename_handles_unicode_and_removes_paths(self):
        self.document.original_filename = "C:\\notes\\révision\r\n.txt"
        self.db.commit()
        response = await self.client.get(self.endpoint, headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-disposition"], "attachment; filename*=utf-8''r%C3%A9vision.txt")
