import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from test_foundation import APITestCase
from app.models.course import Course, Enrollment
from app.models.document import Document
from app.models.role import Role
from app.models.user import User


class CourseAPITests(APITestCase):
    def setUp(self):
        super().setUp()
        lecturer_role = self.db.scalar(select(Role).where(Role.name == "LECTURER"))
        admin_role = self.db.scalar(select(Role).where(Role.name == "ADMIN"))
        self.instructor = User(full_name="Lecturer", email="lecturer@example.com", hashed_password=self.fixture_hash, role_id=lecturer_role.id)
        self.second_instructor = User(full_name="Other Lecturer", email="lecturer2@example.com", hashed_password=self.fixture_hash, role_id=lecturer_role.id)
        self.admin = User(full_name="Admin", email="admin@example.com", hashed_password=self.fixture_hash, role_id=admin_role.id)
        self.db.add_all([self.instructor, self.second_instructor, self.admin])
        self.db.flush()
        self.course = Course(code="ICT101", title="Programming", instructor_id=self.instructor.id)
        self.second_course = Course(code="ICT202", title="Networking", instructor_id=self.second_instructor.id)
        self.db.add_all([self.course, self.second_course])
        self.db.flush()
        self.db.add(Enrollment(course_id=self.course.id, user_id=self.owner.id))
        self.shared = Document(owner_id=self.instructor.id, course_id=self.course.id, visibility="course", original_filename="shared.txt", stored_filename="shared.txt", file_type="txt", file_size_bytes=10, processing_status="extracted", extracted_text="Published lecture content")
        self.student_private = Document(owner_id=self.owner.id, course_id=self.course.id, visibility="private", original_filename="my-notes.txt", stored_filename="my-notes.txt", file_type="txt", file_size_bytes=10, processing_status="extracted", extracted_text="Private student content")
        self.lecturer_private = Document(owner_id=self.instructor.id, course_id=self.course.id, visibility="private", original_filename="draft.txt", stored_filename="draft.txt", file_type="txt", file_size_bytes=10, processing_status="uploaded")
        self.unrelated_shared = Document(owner_id=self.second_instructor.id, course_id=self.second_course.id, visibility="course", original_filename="networking.txt", stored_filename="networking.txt", file_type="txt", file_size_bytes=10, processing_status="uploaded")
        self.db.add_all([self.shared, self.student_private, self.lecturer_private, self.unrelated_shared])
        self.db.commit()
        temporary = tempfile.TemporaryDirectory()
        self.upload_dir = Path(temporary.name)
        self.addCleanup(temporary.cleanup)
        for target in ["app.services.document_service.UPLOAD_DIR", "app.services.extraction_service.UPLOAD_DIR"]:
            patcher = patch(target, str(self.upload_dir))
            patcher.start()
            self.addCleanup(patcher.stop)

    async def upload(self, user=None, **fields):
        return await self.client.post(
            "/documents/upload", headers=self.headers(user), data=fields,
            files={"file": ("lecture.txt", b"An example lecture about programming.", "text/plain")},
        )

    async def test_lecturer_and_admin_can_create_courses(self):
        for user, code in [(self.instructor, " new101 "), (self.admin, "ADM101")]:
            response = await self.client.post("/courses", headers=self.headers(user), json={"code": code, "title": " New course "})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["code"], code.strip().upper())
            self.assertEqual(response.json()["title"], "New course")
            self.assertEqual(response.json()["instructor_id"], str(user.id))

    async def test_student_cannot_create_course_even_with_admin_claim(self):
        response = await self.client.post("/courses", headers=self.headers(role="ADMIN"), json={"code": "NEW101", "title": "Denied"})
        self.assertEqual(response.status_code, 403)

    async def test_duplicate_course_code_is_a_conflict(self):
        response = await self.client.post("/courses", headers=self.headers(self.instructor), json={"code": "ict101", "title": "Duplicate"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.db.query(Course).count(), 2)

    async def test_invalid_course_names_are_rejected(self):
        for code, title in [("bad code", "Title"), ("X", "Title"), ("VALID", "   ")]:
            response = await self.client.post("/courses", headers=self.headers(self.instructor), json={"code": code, "title": title})
            self.assertEqual(response.status_code, 422)

    async def test_catalog_and_my_courses(self):
        catalog = await self.client.get("/courses", headers=self.headers(self.other))
        self.assertEqual(len(catalog.json()), 2)
        for user, expected in [(self.owner, [str(self.course.id)]), (self.instructor, [str(self.course.id)]), (self.other, [])]:
            mine = await self.client.get("/courses?mine=true", headers=self.headers(user))
            self.assertEqual([c["id"] for c in mine.json()], expected)

    async def test_enrollment_is_self_only_and_idempotent(self):
        # Extra query/body identities do not change the authenticated enrollment target.
        path = f"/courses/{self.course.id}/enroll?user_id={self.admin.id}"
        first = await self.client.post(path, headers=self.headers(self.other), json={"user_id": str(self.admin.id)})
        second = await self.client.post(path, headers=self.headers(self.other))
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["user_id"], str(self.other.id))
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(self.db.query(Enrollment).filter(Enrollment.user_id == self.other.id).count(), 1)

    async def test_lecturer_cannot_self_enroll(self):
        response = await self.client.post(f"/courses/{self.course.id}/enroll", headers=self.headers(self.second_instructor))
        self.assertEqual(response.status_code, 403)

    async def test_missing_course(self):
        unknown = uuid.uuid4()
        for method, path in [("GET", f"/courses/{unknown}"), ("POST", f"/courses/{unknown}/enroll")]:
            response = await self.client.request(method, path, headers=self.headers())
            self.assertEqual(response.status_code, 404)

    async def test_enrolled_student_reads_only_published_material(self):
        listing = await self.client.get(f"/documents?course_id={self.course.id}", headers=self.headers())
        self.assertEqual({d["id"] for d in listing.json()}, {str(self.shared.id), str(self.student_private.id)})
        text = await self.client.get(f"/documents/{self.shared.id}/text", headers=self.headers())
        self.assertEqual(text.status_code, 200, text.text)
        self.assertEqual(text.json()["text"], "Published lecture content")
        draft = await self.client.get(f"/documents/{self.lecturer_private.id}", headers=self.headers())
        self.assertEqual(draft.status_code, 404)

    async def test_instructor_and_admin_cannot_read_student_private_material(self):
        for user in [self.instructor, self.admin]:
            response = await self.client.get(f"/documents/{self.student_private.id}/text", headers=self.headers(user))
            self.assertEqual(response.status_code, 404)

    async def test_unenrolled_user_cannot_read_shared_material(self):
        response = await self.client.get(f"/documents/{self.shared.id}/text", headers=self.headers(self.other))
        self.assertEqual(response.status_code, 404)

    async def test_enrollment_revocation_removes_shared_access_but_keeps_owned_notes(self):
        response = await self.client.delete(f"/courses/{self.course.id}/enroll", headers=self.headers())
        self.assertEqual(response.status_code, 204)
        response = await self.client.get(f"/documents/{self.shared.id}/text", headers=self.headers())
        self.assertEqual(response.status_code, 404)
        response = await self.client.get(f"/documents/{self.student_private.id}/text", headers=self.headers())
        self.assertEqual(response.status_code, 200)
        repeated = await self.client.delete(f"/courses/{self.course.id}/enroll", headers=self.headers())
        self.assertEqual(repeated.status_code, 204)

    async def test_enrollment_cannot_be_removed_for_another_user(self):
        await self.client.delete(f"/courses/{self.course.id}/enroll?user_id={self.owner.id}", headers=self.headers(self.other))
        self.assertIsNotNone(self.db.scalar(select(Enrollment).where(Enrollment.user_id == self.owner.id)))

    async def test_enrolled_student_cannot_process_instructor_material(self):
        for operation in ["extract", "chunk"]:
            response = await self.client.post(f"/documents/{self.shared.id}/{operation}", headers=self.headers())
            self.assertEqual(response.status_code, 403)

    async def test_student_upload_is_private_by_default(self):
        response = await self.upload(course_id=str(self.course.id))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["visibility"], "private")
        self.assertEqual(response.json()["course_id"], str(self.course.id))
        self.assertEqual(len(list(self.upload_dir.iterdir())), 1)

    async def test_existing_upload_without_course_remains_supported(self):
        response = await self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["course_id"])
        self.assertEqual(response.json()["visibility"], "private")

    async def test_instructor_publishes_and_student_reads_extracted_text(self):
        uploaded = await self.upload(self.instructor, course_id=str(self.course.id), visibility="course")
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        document_id = uploaded.json()["id"]
        extracted = await self.client.post(f"/documents/{document_id}/extract", headers=self.headers(self.instructor))
        self.assertEqual(extracted.status_code, 200, extracted.text)
        student_text = await self.client.get(f"/documents/{document_id}/text", headers=self.headers())
        self.assertEqual(student_text.json()["text"], "An example lecture about programming.")

    async def test_student_cannot_publish_or_upload_into_unenrolled_course(self):
        for user, fields in [(self.owner, {"course_id": str(self.course.id), "visibility": "course"}), (self.other, {"course_id": str(self.course.id)}), (self.owner, {"course_id": str(self.second_course.id)})]:
            response = await self.upload(user, **fields)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    async def test_other_instructor_cannot_publish_course_material(self):
        response = await self.upload(self.second_instructor, course_id=str(self.course.id), visibility="course")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    async def test_shared_upload_requires_course_and_valid_visibility(self):
        response = await self.upload(self.instructor, visibility="course")
        self.assertEqual(response.status_code, 400)
        response = await self.upload(self.instructor, visibility="everyone")
        self.assertEqual(response.status_code, 422)
        response = await self.upload(course_id=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    async def test_failed_database_insert_cleans_up_upload(self):
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("test failure")):
            with self.assertRaises(SQLAlchemyError):
                await self.upload()
        self.assertEqual(list(self.upload_dir.iterdir()), [])
        self.assertIsNotNone(self.db.scalar(select(User).where(User.id == self.owner.id)))

    def test_database_rejects_shared_document_without_course(self):
        self.db.add(Document(owner_id=self.owner.id, visibility="course", original_filename="invalid.txt", stored_filename="invalid.txt", file_type="txt", file_size_bytes=1))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()
