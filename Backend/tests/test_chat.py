from unittest.mock import patch
from test_foundation import APITestCase
from app.models.chunk import DocumentChunk
from app.schemas.search import SearchResponse, SearchResult
from app.schemas.source import SourceLocation


class ChatAPITests(APITestCase):
    def setUp(self):
        super().setUp()
        self.document.processing_status = "embedded"
        self.chunk = DocumentChunk(document_id=self.document.id, chunk_index=0,
            content="A subnet mask separates a network address from its host portion.",
            source_location={"kind": "pdf_page", "page": 3})
        self.db.add(self.chunk)
        self.db.commit()
        self.source = SearchResult(chunk_id=self.chunk.id, document_id=self.document.id, course_id=None,
            document_name=self.document.original_filename, chunk_index=0, text=self.chunk.content,
            similarity=0.8, source_location=SourceLocation(kind="pdf_page", page=3))
        self.results = SearchResponse(query="Question", status="matches", results=[self.source])
        patcher = patch("app.services.search_service.search", return_value=self.results)
        self.search = patcher.start()
        self.addCleanup(patcher.stop)

    def valid_answer(self):
        return {"status": "answered", "claims": [{"text": "It separates the network address from its host portion.",
            "evidence": [{"source_id": "S1", "quote": "A subnet mask separates a network address from its host portion."}]}]}

    async def chat(self, user=None, **changes):
        return await self.client.post("/chat", headers=self.headers(user), json={"query": "What does a subnet mask do?", **changes})

    async def test_authentication_and_validation(self):
        response = await self.client.post("/chat", json={"query": "Question"})
        self.assertEqual(response.status_code, 401)
        response = await self.chat(query=" ")
        self.assertEqual(response.status_code, 422)

    async def test_answer_uses_server_citations_and_exact_source_quote(self):
        with patch("app.services.llm_service.generate_answer", return_value=self.valid_answer()) as generate:
            response = await self.chat()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "answered")
        self.assertTrue(body["answer"].endswith("[S1]"))
        self.assertEqual(body["citations"][0]["document_id"], str(self.document.id))
        self.assertEqual(body["citations"][0]["source_location"]["page"], 3)
        self.assertEqual(generate.call_args.args[1], [{"source_id": "S1", "text": self.chunk.content}])

    async def test_empty_search_skips_generation(self):
        self.search.return_value = SearchResponse(query="Question", status="no_searchable_materials", results=[])
        with patch("app.services.llm_service.generate_answer") as generate:
            response = await self.chat()
        self.assertEqual(response.json()["status"], "no_searchable_materials")
        self.assertEqual(response.json()["citations"], [])
        generate.assert_not_called()

    async def test_no_matches_skips_generation(self):
        self.search.return_value = SearchResponse(query="Question", status="no_matches", results=[])
        with patch("app.services.llm_service.generate_answer") as generate:
            response = await self.chat()
        self.assertEqual(response.json()["status"], "not_found")
        generate.assert_not_called()

    async def test_model_can_abstain_despite_similar_passage(self):
        with patch("app.services.llm_service.generate_answer", return_value={"status": "not_found", "claims": []}):
            response = await self.chat()
        self.assertEqual(response.json()["status"], "not_found")
        self.assertEqual(response.json()["citations"], [])

    async def test_unknown_source_and_fabricated_quote_are_rejected(self):
        for evidence in [{"source_id": "S99", "quote": self.chunk.content}, {"source_id": "S1", "quote": "Invented academic fact"}]:
            answer = self.valid_answer()
            answer["claims"][0]["evidence"] = [evidence]
            with patch("app.services.llm_service.generate_answer", return_value=answer):
                response = await self.chat()
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("Invented academic fact", response.text)

    async def test_malformed_and_uncited_answers_are_rejected(self):
        for answer in [None, {"status": "answered", "claims": []}, {"status": "not_found", "claims": self.valid_answer()["claims"]}, {"status": "answered", "claims": [{"text": "Unsupported", "evidence": []}]}]:
            with patch("app.services.llm_service.generate_answer", return_value=answer):
                response = await self.chat()
            self.assertEqual(response.status_code, 502)

    async def test_model_cannot_inject_its_own_citation_markers(self):
        answer = self.valid_answer()
        answer["claims"][0]["text"] += " [S99]"
        with patch("app.services.llm_service.generate_answer", return_value=answer):
            response = await self.chat()
        self.assertEqual(response.status_code, 502)

    async def test_quote_whitespace_can_be_normalised(self):
        answer = self.valid_answer()
        answer["claims"][0]["evidence"][0]["quote"] = self.chunk.content.replace(" ", "\n")
        with patch("app.services.llm_service.generate_answer", return_value=answer):
            response = await self.chat()
        self.assertEqual(response.status_code, 200)

    async def test_forged_private_search_result_is_rechecked_before_model(self):
        with patch("app.services.llm_service.generate_answer") as generate:
            response = await self.chat(self.other)
        self.assertEqual(response.status_code, 409)
        generate.assert_not_called()

    async def test_changed_chunk_during_generation_is_not_returned(self):
        def changed(*args):
            self.chunk.content = "Changed source text"
            self.db.commit()
            return self.valid_answer()
        with patch("app.services.llm_service.generate_answer", side_effect=changed):
            response = await self.chat()
        self.assertEqual(response.status_code, 409)

    async def test_failed_document_during_generation_is_not_returned(self):
        def changed(*args):
            self.document.processing_status = "failed"
            self.db.commit()
            return self.valid_answer()
        with patch("app.services.llm_service.generate_answer", side_effect=changed):
            response = await self.chat()
        self.assertEqual(response.status_code, 409)

    async def test_scope_and_threshold_are_forwarded_to_search(self):
        with patch("app.services.llm_service.generate_answer", return_value=self.valid_answer()):
            response = await self.chat(document_id=str(self.document.id), min_similarity=0.6, top_k=3)
        self.assertEqual(response.status_code, 200)
        request = self.search.call_args.args[1]
        self.assertEqual(request.document_id, self.document.id)
        self.assertEqual(request.min_similarity, 0.6)
        self.assertEqual(request.top_k, 3)

    async def test_generation_reports_missing_configuration(self):
        with patch("app.services.llm_service.settings.LLM_PROVIDER", "disabled"):
            response = await self.chat()
        self.assertEqual(response.status_code, 503)
