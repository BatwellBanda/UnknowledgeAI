import json
import unittest
from unittest.mock import patch
import httpx
from fastapi import HTTPException
from test_foundation import APITestCase  # Initialise isolated test settings first.
from app.services import llm_service


class OllamaTransportTests(unittest.TestCase):
    def run_transport(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
        with patch("app.services.llm_service.httpx.Client", return_value=client):
            return llm_service.generate_answer("Question", [{"source_id": "S1", "text": "Source text"}])

    def test_structured_request_and_response(self):
        answer = {"status": "not_found", "claims": []}

        def handle(request):
            body = json.loads(request.content)
            self.assertEqual(request.url.path, "/api/chat")
            self.assertEqual(request.url.host, "127.0.0.1")
            self.assertFalse(body["stream"])
            self.assertEqual(body["options"]["temperature"], 0)
            self.assertIn("claims", body["format"]["properties"])
            self.assertIn("untrusted", body["messages"][0]["content"])
            self.assertEqual(json.loads(body["messages"][1]["content"])["sources"], [{"source_id": "S1", "text": "Source text"}])
            return httpx.Response(200, json={"done": True, "done_reason": "stop", "message": {"content": json.dumps(answer)}})

        self.assertEqual(self.run_transport(handle), answer)

    def test_http_errors_are_sanitised(self):
        for status, expected in [(404, 503), (500, 503), (429, 503)]:
            with self.assertRaises(HTTPException) as caught:
                self.run_transport(lambda request: httpx.Response(status, json={"error": "private internal details"}))
            self.assertEqual(caught.exception.status_code, expected)
            self.assertNotIn("private internal details", caught.exception.detail)

    def test_connection_and_timeout_errors(self):
        for exception, expected in [(httpx.ConnectError("private endpoint"), 503), (httpx.ReadTimeout("private details"), 504)]:
            def fail(request):
                raise exception
            with self.assertRaises(HTTPException) as caught:
                self.run_transport(fail)
            self.assertEqual(caught.exception.status_code, expected)
            self.assertNotIn("private", caught.exception.detail)

    def test_malformed_or_truncated_generation_is_rejected(self):
        for body in [
            {"done": False, "message": {"content": "{}"}},
            {"done": True, "done_reason": "length", "message": {"content": "{}"}},
            {"done": True, "message": {"content": "invalid json"}},
            {"done": True, "message": {"content": "[]"}},
            {"done": True, "message": {"content": None}},
            {"done": True},
        ]:
            with self.assertRaises(HTTPException) as caught:
                self.run_transport(lambda request: httpx.Response(200, json=body))
            self.assertEqual(caught.exception.status_code, 502)
