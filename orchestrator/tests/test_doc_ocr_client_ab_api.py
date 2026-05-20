from __future__ import annotations

import tempfile
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

schemas_common = types.ModuleType("app.schemas.common")


class AgentResult:
    def __init__(self, ok: bool, data: dict, error: str | None = None):
        self.ok = ok
        self.data = data
        self.error = error


schemas_common.AgentResult = AgentResult
sys.modules.setdefault("app.schemas.common", schemas_common)

from app.agents.doc_ocr import client as client_module
from app.agents.doc_ocr.client import DocOCRClient


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeAsyncClient:
    calls: list[dict] = []
    responses: list[FakeResponse] = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


class DocOCRClientABAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeAsyncClient.calls = []
        FakeAsyncClient.responses = []

    async def test_real_flow_uses_api_key_for_conversation_and_token_for_upload(self) -> None:
        FakeAsyncClient.responses = [
            FakeResponse({"conversation_id": "conv-1"}),
            FakeResponse({"id": "file-1"}),
            FakeResponse({"ok": True}),
        ]
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"hello")
            f.flush()
            client = DocOCRClient(
                base_url="http://ab",
                authorization="sk-test",
                access_key="ak-test",
                secret_key="secret-test",
                x_authorization="private-token",
                app_id="app-1",
            )

            with patch.object(client_module.httpx, "AsyncClient", FakeAsyncClient):
                result = await client.run_doc_ocr_real(local_file_path=f.name, options={"query": "ocr", "stream": False})

        self.assertTrue(result.ok)
        self.assertEqual([call["url"] for call in FakeAsyncClient.calls], [
            "http://ab/api/ai_apaas/v1/app/conversation",
            "http://ab/api/ai_apaas/v1/app/conversation/file/upload",
            "http://ab/api/ai_apaas/v1/app/conversation/runs",
        ])

        conversation_headers = FakeAsyncClient.calls[0]["headers"]
        upload_headers = FakeAsyncClient.calls[1]["headers"]
        run_headers = FakeAsyncClient.calls[2]["headers"]

        self.assertEqual(conversation_headers["Authorization"], "Bearer sk-test")
        self.assertEqual(run_headers["Authorization"], "Bearer sk-test")
        self.assertNotIn("Token", conversation_headers)
        self.assertNotIn("Token", run_headers)

        self.assertNotIn("Authorization", upload_headers)
        self.assertEqual(upload_headers["Access-Key"], "ak-test")
        self.assertEqual(upload_headers["X-Authorization"], "private-token")
        self.assertIn("X-Bce-Request-ID", upload_headers)
        self.assertIn("Sign-Time", upload_headers)
        self.assertIn("Token", upload_headers)

    async def test_many_flow_generates_upload_signature_per_file(self) -> None:
        FakeAsyncClient.responses = [
            FakeResponse({"conversation_id": "conv-1"}),
            FakeResponse({"id": "file-1"}),
            FakeResponse({"id": "file-2"}),
            FakeResponse({"ok": True}),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            first = Path(tmpdir) / "first.txt"
            second = Path(tmpdir) / "second.txt"
            first.write_text("one", encoding="utf-8")
            second.write_text("two", encoding="utf-8")
            client = DocOCRClient(
                base_url="http://ab",
                authorization="Bearer sk-test",
                access_key="ak-test",
                secret_key="secret-test",
                app_id="app-1",
            )

            with patch.object(client_module.httpx, "AsyncClient", FakeAsyncClient):
                result = await client.run_doc_ocr_real_many(
                    local_file_paths=[str(first), str(second)],
                    options={"query": "ocr", "stream": False},
                )

        self.assertTrue(result.ok)
        upload_headers = [FakeAsyncClient.calls[1]["headers"], FakeAsyncClient.calls[2]["headers"]]
        self.assertEqual(upload_headers[0]["Access-Key"], "ak-test")
        self.assertEqual(upload_headers[1]["Access-Key"], "ak-test")
        self.assertNotIn("Authorization", upload_headers[0])
        self.assertNotIn("Authorization", upload_headers[1])
        self.assertNotEqual(upload_headers[0]["X-Bce-Request-ID"], upload_headers[1]["X-Bce-Request-ID"])


if __name__ == "__main__":
    unittest.main()
