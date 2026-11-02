import unittest
from unittest.mock import Mock, patch

from embedding_fixtures import CharacterTokenizer, embedding_model
from test_foundation import APITestCase
from app.models.chunk import DocumentChunk
from app.services.chunking_service import located_chunks, token_chunk_spans


class TokenWindowTests(unittest.TestCase):
    def test_dense_unicode_and_long_unbroken_text_keep_every_character(self):
        tokenizer = CharacterTokenizer()
        for text in ["\u7f51\u7edc\u5730\u5740" * 160, "abcd" * 600, "a,b;c: " * 300]:
            covered = set()
            spans = list(token_chunk_spans(text, tokenizer, 40))
            for start, end in spans:
                self.assertGreater(end, start)
                self.assertLessEqual(len(tokenizer.encode(text[start:end].strip())), 40)
                covered.update(range(start, end))
            self.assertEqual(covered, set(range(len(text))))
            self.assertEqual(spans[-1][1], len(text))

    def test_special_tokens_count_and_words_overlap_without_splitting(self):
        tokenizer = CharacterTokenizer()
        text = " ".join(f"word{i:02}" for i in range(50))
        spans = list(token_chunk_spans(text, tokenizer, 40))
        seen = []
        for start, end in spans:
            words = text[start:end].split()
            self.assertTrue(all(word in text.split() for word in words))
            self.assertLessEqual(len(text[start:end].strip()), 38)
            seen.extend(words)
        self.assertEqual(set(seen), set(text.split()))
        self.assertGreater(len(seen), len(text.split()))

    def test_trimmed_txt_locations_remain_exact_and_pages_never_mix(self):
        segments = [
            {"text": "\n\n   First line\nSecond line\n  ",
             "location": {"kind": "txt_lines", "line_start": 10, "line_end": 14}},
            {"text": "Other source", "location": {"kind": "pdf_page", "page": 7}},
        ]
        pieces = located_chunks(segments, tokenizer=CharacterTokenizer(), max_tokens=256)
        self.assertEqual(pieces[0]["content"], "First line\nSecond line")
        self.assertEqual(pieces[0]["source_location"]["line_start"], 12)
        self.assertEqual(pieces[0]["source_location"]["line_end"], 13)
        self.assertEqual(pieces[1]["source_location"], {"kind": "pdf_page", "page": 7})

    def test_invalid_budgets_and_empty_text(self):
        tokenizer = CharacterTokenizer()
        for budget, size, overlap in [(0, 10, 1), (5, 0, 0), (5, 10, 10), (5, 10, -1), (2, 10, 0)]:
            with self.assertRaises(ValueError):
                list(token_chunk_spans("abc", tokenizer, budget, size, overlap))
        self.assertEqual(list(token_chunk_spans("", tokenizer, 5)), [])
        with self.assertRaises(ValueError):
            located_chunks([], tokenizer=tokenizer)


class TokenChunkingAPITests(APITestCase):
    def extracted_document(self):
        self.document.extracted_text = "dense " * 300 + "Important ending fact."
        self.document.processing_status = "extracted"
        self.db.commit()

    async def test_chunk_endpoint_uses_model_budget_and_preserves_tail(self):
        self.extracted_document()
        model = embedding_model()
        with patch("app.services.embedding_service.get_model", return_value=model):
            response = await self.client.post(f"/documents/{self.document.id}/chunk", headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        chunks = response.json()
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(model.tokenizer.encode(c["content"])) <= model.max_seq_length for c in chunks))
        self.assertTrue(chunks[-1]["content"].endswith("Important ending fact."))

    async def test_unavailable_model_does_not_remove_existing_chunks(self):
        self.extracted_document()
        chunk = DocumentChunk(document_id=self.document.id, chunk_index=0, content="Last good passage")
        self.db.add(chunk)
        self.db.commit()
        old_id = chunk.id
        with patch("app.services.embedding_service.get_model", side_effect=RuntimeError("private/cache/path")):
            with self.assertLogs("app.services.chunking_service", level="ERROR"):
                response = await self.client.post(f"/documents/{self.document.id}/chunk", headers=self.headers())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private/cache/path", response.text)
        self.assertEqual(self.db.query(DocumentChunk).one().id, old_id)
        self.assertEqual(self.document.processing_status, "extracted")

    async def test_overlong_legacy_chunk_is_rejected_before_embedding(self):
        self.document.processing_status = "chunked"
        self.db.add(DocumentChunk(document_id=self.document.id, chunk_index=0, content="x" * 300))
        self.db.commit()
        model = embedding_model()
        model.encode = Mock()
        with patch("app.services.embedding_service.get_model", return_value=model):
            response = await self.client.post(f"/documents/{self.document.id}/embed", headers=self.headers())
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("re-chunk", response.json()["detail"])
        model.encode.assert_not_called()
        self.assertEqual(self.document.processing_status, "chunked")
        self.assertIsNone(self.db.query(DocumentChunk).one().embedding)
