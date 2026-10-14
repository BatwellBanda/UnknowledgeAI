"""Regression tests with an isolated in-memory database; no development data is used."""
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

# Set these before importing app settings or the engine. Never load a real DB URL.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "foundation-tests-only-secret-not-for-deployment"

import bcrypt
import httpx
from fastapi import HTTPException
from jose import jwt
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import (
    ALGORITHM, create_access_token, hash_password, require_role, verify_password,
)
from app.main import app
from app.models.document import Document
from app.models.role import Role
from app.models.user import User
from app.schemas.user import UserRegister
from app.services.auth_service import register_user
from database.connection import Base, get_db

PASSWORD = "a long test passphrase"


class PasswordTests(unittest.TestCase):
    def test_existing_bcrypt_hashes_still_work(self):
        existing_hash = bcrypt.hashpw(b"old-short", bcrypt.gensalt()).decode()
        self.assertTrue(verify_password("old-short", existing_hash))
        self.assertFalse(verify_password("wrong", existing_hash))

    def test_different_long_passwords_do_not_match_same_prefix(self):
        prefix = "a" * 72
        stored = hash_password(prefix)
        self.assertTrue(verify_password(prefix, stored))
        self.assertFalse(verify_password(prefix + "x", stored))
        self.assertFalse(verify_password(prefix + "y", stored))
        with self.assertRaises(ValueError):
            hash_password(prefix + "x")

    def test_byte_limit_includes_multibyte_characters(self):
        with self.assertRaises(ValueError):
            hash_password("é" * 37)
        stored = hash_password("é" * 36)
        self.assertTrue(verify_password("é" * 36, stored))

    def test_malformed_stored_hash_returns_false(self):
        self.assertFalse(verify_password(PASSWORD, "not-a-bcrypt-hash"))


class FoundationAPITests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_hash = hash_password(PASSWORD)

    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine, tables=[Role.__table__, User.__table__, Document.__table__])
        self.db = Session(self.engine)
        self.student_role = Role(name="STUDENT")
        self.db.add_all([self.student_role, Role(name="LECTURER"), Role(name="ADMIN")])
        self.db.flush()
        self.owner = User(full_name="Owner", email="owner@example.com", hashed_password=self.fixture_hash, role_id=self.student_role.id)
        self.other = User(full_name="Other", email="other@example.com", hashed_password=self.fixture_hash, role_id=self.student_role.id)
        self.db.add_all([self.owner, self.other])
        self.db.flush()
        self.document = Document(owner_id=self.owner.id, original_filename="private.txt", stored_filename="private.txt", file_type="txt", file_size_bytes=10, processing_status="uploaded")
        self.db.add(self.document)
        self.db.commit()

        def test_db():
            yield self.db

        app.dependency_overrides[get_db] = test_db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()

    def headers(self, user=None, **claims):
        data = {"sub": str((user or self.owner).id), **claims}
        return {"Authorization": f"Bearer {create_access_token(data)}"}

    def registration(self, **changes):
        return {"full_name": " New Student ", "email": "new@example.com", "password": PASSWORD, **changes}

    async def test_registration_defaults_to_student(self):
        response = await self.client.post("/auth/register", json=self.registration())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["role"], "STUDENT")
        self.assertEqual(response.json()["full_name"], "New Student")
        self.assertNotIn("password", response.json())
        user = self.db.scalar(select(User).where(User.email == "new@example.com"))
        self.assertTrue(verify_password(PASSWORD, user.hashed_password))

    async def test_existing_student_role_payload_remains_supported(self):
        response = await self.client.post("/auth/register", json=self.registration(role="student"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["role"], "STUDENT")

    async def test_privileged_roles_are_rejected_without_creating_user(self):
        for role in ["ADMIN", "admin", "LECTURER", "INVALID"]:
            with self.subTest(role=role):
                response = await self.client.post("/auth/register", json=self.registration(role=role))
                self.assertEqual(response.status_code, 422)
                self.assertIsNone(self.db.scalar(select(User).where(User.email == "new@example.com")))

    def test_service_also_rejects_privileged_registration(self):
        data = UserRegister.model_construct(**self.registration(role="ADMIN"))
        with self.assertRaises(HTTPException) as caught:
            register_user(self.db, data)
        self.assertEqual(caught.exception.status_code, 400)

    async def test_registration_validation(self):
        for fields in [{"password": "short"}, {"password": "x" * 73}, {"password": "é" * 37}, {"full_name": "   "}, {"full_name": "x" * 151}, {"email": "invalid"}]:
            with self.subTest(fields=fields):
                response = await self.client.post("/auth/register", json=self.registration(**fields))
                self.assertEqual(response.status_code, 422)

    async def test_duplicate_registration(self):
        response = await self.client.post("/auth/register", json=self.registration(email=self.owner.email))
        self.assertEqual(response.status_code, 400)

    def test_registration_unique_conflict_rolls_back_session(self):
        # Simulate a stale pre-check; the real database rejects the duplicate insert.
        original_query = self.db.query
        stale_check = Mock()
        stale_check.filter.return_value.first.return_value = None
        first_user_query = True

        def query(*entities, **kwargs):
            nonlocal first_user_query
            if entities == (User,) and first_user_query:
                first_user_query = False
                return stale_check
            return original_query(*entities, **kwargs)

        data = UserRegister(**self.registration(email=self.owner.email))
        with patch.object(self.db, "query", side_effect=query):
            with self.assertRaises(HTTPException) as caught:
                register_user(self.db, data)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIsNotNone(self.db.scalar(select(User).where(User.id == self.owner.id)))

    async def test_missing_seeded_student_role_is_controlled(self):
        # Rename instead of deleting an in-use role, preserving foreign keys.
        self.student_role.name = "UNSEEDED"
        self.db.commit()
        response = await self.client.post("/auth/register", json=self.registration())
        self.assertEqual(response.status_code, 503)

    async def test_login_and_current_user(self):
        login = await self.client.post("/auth/login", json={"email": self.owner.email, "password": PASSWORD})
        self.assertEqual(login.status_code, 200, login.text)
        profile = await self.client.get("/users/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
        self.assertEqual(profile.status_code, 200, profile.text)
        self.assertEqual(profile.json()["id"], str(self.owner.id))
        self.assertEqual(profile.json()["role"], "STUDENT")
        self.assertNotIn("hashed_password", profile.json())

    async def test_invalid_credentials(self):
        for email, password in [(self.owner.email, "wrong"), ("missing@example.com", PASSWORD), (self.owner.email, "x" * 73)]:
            response = await self.client.post("/auth/login", json={"email": email, "password": password})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["detail"], "Invalid email or password")

    async def test_missing_or_malformed_authorization(self):
        for headers in [{}, {"Authorization": "Basic nonsense"}, {"Authorization": "Bearer nonsense"}]:
            response = await self.client.get("/users/me", headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["www-authenticate"], "Bearer")

    async def test_token_claim_validation(self):
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        cases = [
            {"sub": "not-a-uuid", "exp": future},
            {"sub": 123, "exp": future},
            {"exp": future},
            {"sub": str(self.owner.id)},
            {"sub": str(uuid.uuid4()), "exp": future},
            {"sub": str(self.owner.id), "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        ]
        for claims in cases:
            with self.subTest(claims=claims):
                token = jwt.encode(claims, settings.SECRET_KEY, algorithm=ALGORITHM)
                response = await self.client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 401, response.text)

    async def test_wrong_signature_is_rejected(self):
        token = jwt.encode({"sub": str(self.owner.id), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, "wrong-secret", algorithm=ALGORITHM)
        response = await self.client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 401)

    async def test_database_role_wins_over_token_role(self):
        profile = await self.client.get("/users/me", headers=self.headers(role="ADMIN"))
        self.assertEqual(profile.json()["role"], "STUDENT")
        with self.assertRaises(HTTPException) as caught:
            require_role("ADMIN")(self.owner)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIs(require_role("STUDENT")(self.owner), self.owner)

    async def test_owner_can_list_and_inspect_document(self):
        listing = await self.client.get("/documents", headers=self.headers())
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual([d["id"] for d in listing.json()], [str(self.document.id)])
        detail = await self.client.get(f"/documents/{self.document.id}", headers=self.headers())
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn("stored_filename", detail.json())

    async def test_other_user_cannot_list_or_inspect_private_document(self):
        listing = await self.client.get("/documents", headers=self.headers(self.other))
        self.assertEqual(listing.json(), [])
        detail = await self.client.get(f"/documents/{self.document.id}", headers=self.headers(self.other))
        self.assertEqual(detail.status_code, 404)

    async def test_other_user_cannot_extract_private_document(self):
        extractor = Mock()
        with patch.dict("app.services.extraction_service.EXTRACTORS", {"txt": extractor}):
            response = await self.client.post(f"/documents/{self.document.id}/extract", headers=self.headers(self.other))
            self.assertEqual(response.status_code, 403)
            extractor.assert_not_called()
        self.assertEqual(self.document.processing_status, "uploaded")

    async def test_document_list_pagination_is_bounded(self):
        for query in ["limit=101", "limit=0", "offset=-1"]:
            response = await self.client.get(f"/documents?{query}", headers=self.headers())
            self.assertEqual(response.status_code, 422)
        response = await self.client.get("/documents?offset=1", headers=self.headers())
        self.assertEqual(response.json(), [])


if __name__ == "__main__":
    unittest.main()
