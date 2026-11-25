import json
import math
from types import SimpleNamespace
from unittest.mock import Mock, patch
from test_foundation import APITestCase
from app.models.chunk import DocumentChunk


class SearchAPITests(APITestCase):
    def setUp(self):
        super().setUp()
        # SQLite handles API validation/failure tests; real cosine SQL is tested separately.
        with self.engine.connect() as connection:
            connection.connection.driver_connection.create_function(
                "vector_norm", 1, lambda value: math.hypot(*json.loads(value)) if value else None,
            )

    def add_searchable_chunk(self):
        self.document.processing_status = "embedded"
        self.db.add(DocumentChunk(document_id=self.document.id, chunk_index=0, content="Searchable fixture", embedding=[1.0] + [0.0] * 383))
        self.db.commit()

    async def test_search_requires_authentication(self):
        response = await self.client.post("/search", json={"query": "Question"})
        self.assertEqual(response.status_code, 401)

    async def test_search_input_validation(self):
        for data in [{"query": " "}, {"query": "x" * 2001}, {"query": "Question", "top_k": 21}, {"query": "Question", "top_k": 0}, {"query": "Question", "min_similarity": 2}, {"query": "Question", "course_id": "invalid"}]:
            response = await self.client.post("/search", headers=self.headers(), json=data)
            self.assertEqual(response.status_code, 422)

    async def test_empty_library_does_not_load_model(self):
        with patch("app.services.embedding_service.get_model") as load:
            response = await self.client.post("/search", headers=self.headers(), json={"query": " Question "})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"query": "Question", "status": "no_searchable_materials", "results": []})
            load.assert_not_called()

    async def test_private_library_does_not_load_model_for_other_user(self):
        self.add_searchable_chunk()
        with patch("app.services.embedding_service.get_model") as load:
            response = await self.client.post("/search", headers=self.headers(self.other), json={"query": "Question"})
            self.assertEqual(response.json()["status"], "no_searchable_materials")
            load.assert_not_called()

    async def test_model_failure_is_controlled(self):
        self.add_searchable_chunk()
        with patch("app.services.embedding_service.get_model", side_effect=RuntimeError("private/cache/path")):
            with self.assertLogs("app.services.search_service", level="ERROR"):
                response = await self.client.post("/search", headers=self.headers(), json={"query": "Question"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private/cache/path", response.text)

    async def test_overlong_tokenized_query_is_rejected(self):
        self.add_searchable_chunk()
        model = SimpleNamespace(tokenizer=SimpleNamespace(encode=lambda *args, **kwargs: [1] * 257), max_seq_length=256, encode=Mock())
        with patch("app.services.embedding_service.get_model", return_value=model):
            response = await self.client.post("/search", headers=self.headers(), json={"query": "Question"})
        self.assertEqual(response.status_code, 400)
        model.encode.assert_not_called()

    async def test_invalid_query_vectors_are_rejected(self):
        self.add_searchable_chunk()
        for vector in [[1.0], [0.0] * 384, [float("nan")] * 384]:
            model = SimpleNamespace(tokenizer=SimpleNamespace(encode=lambda *args, **kwargs: [1]), max_seq_length=256, encode=lambda *args, **kwargs: [vector])
            with patch("app.services.embedding_service.get_model", return_value=model):
                with self.assertLogs("app.services.search_service", level="ERROR"):
                    response = await self.client.post("/search", headers=self.headers(), json={"query": "Question"})
            self.assertEqual(response.status_code, 503)
