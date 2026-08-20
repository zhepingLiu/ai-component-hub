from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.generic_agent import client as client_module
from app.agents.generic_agent.client import GenericAgentClient


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


class GenericAgentClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeAsyncClient.calls = []
        FakeAsyncClient.responses = []

    async def test_run_without_files_skips_upload(self) -> None:
        FakeAsyncClient.responses = [
            FakeResponse({"conversation_id": "conv-1"}),
            FakeResponse({"ok": True}),
        ]
        client = GenericAgentClient(
            base_url="http://ab",
            authorization="sk-test",
            app_id="app-1",
        )

        with patch.object(client_module.httpx, "AsyncClient", FakeAsyncClient):
            result = await client.run(
                local_file_paths=[],
                inputs={"question": "hello"},
                options={"stream": False},
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            [call["url"] for call in FakeAsyncClient.calls],
            [
                "http://ab/api/ai_apaas/v1/app/conversation",
                "http://ab/api/ai_apaas/v1/app/conversation/runs",
            ],
        )
        run_payload = FakeAsyncClient.calls[1]["json"]
        self.assertEqual(run_payload["app_id"], "app-1")
        self.assertEqual(run_payload["conversation_id"], "conv-1")
        self.assertEqual(run_payload["inputs"], {"question": "hello"})
        self.assertEqual(run_payload["stream"], False)
        self.assertNotIn("file_ids", run_payload)

    async def test_run_with_one_file_uploads_then_runs_with_file_id(self) -> None:
        FakeAsyncClient.responses = [
            FakeResponse({"conversation_id": "conv-1"}),
            FakeResponse({"id": "file-1"}),
            FakeResponse({"ok": True}),
        ]
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"hello")
            f.flush()
            client = GenericAgentClient(
                base_url="http://ab",
                authorization="sk-test",
                access_key="ak-test",
                secret_key="secret-test",
                x_authorization="private-token",
                app_id="app-1",
            )

            with patch.object(client_module.httpx, "AsyncClient", FakeAsyncClient):
                result = await client.run(local_file_paths=[f.name], inputs={}, options={})

        self.assertTrue(result.ok)
        self.assertEqual(len(FakeAsyncClient.calls), 3)
        upload_headers = FakeAsyncClient.calls[1]["headers"]
        self.assertNotIn("Authorization", upload_headers)
        self.assertEqual(upload_headers["Access-Key"], "ak-test")
        self.assertEqual(upload_headers["X-Authorization"], "private-token")
        self.assertIn("Token", upload_headers)
        self.assertEqual(FakeAsyncClient.calls[2]["json"]["file_ids"], ["file-1"])

    async def test_run_with_many_files_generates_upload_signature_per_file(self) -> None:
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
            client = GenericAgentClient(
                base_url="http://ab",
                authorization="Bearer sk-test",
                access_key="ak-test",
                secret_key="secret-test",
                app_id="app-1",
            )

            with patch.object(client_module.httpx, "AsyncClient", FakeAsyncClient):
                result = await client.run(local_file_paths=[str(first), str(second)], inputs={}, options={})

        self.assertTrue(result.ok)
        upload_headers = [FakeAsyncClient.calls[1]["headers"], FakeAsyncClient.calls[2]["headers"]]
        self.assertEqual(upload_headers[0]["Access-Key"], "ak-test")
        self.assertEqual(upload_headers[1]["Access-Key"], "ak-test")
        self.assertNotEqual(upload_headers[0]["X-Bce-Request-ID"], upload_headers[1]["X-Bce-Request-ID"])
        self.assertEqual(FakeAsyncClient.calls[3]["json"]["file_ids"], ["file-1", "file-2"])

    async def test_missing_api_key_fails_before_request(self) -> None:
        client = GenericAgentClient(base_url="http://ab", app_id="app-1")

        result = await client.run(local_file_paths=[], inputs={}, options={})

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Agent api_key is not configured")
        self.assertEqual(FakeAsyncClient.calls, [])

    async def test_missing_upload_keys_fails_when_files_are_present(self) -> None:
        with tempfile.NamedTemporaryFile() as f:
            client = GenericAgentClient(base_url="http://ab", authorization="sk-test", app_id="app-1")

            result = await client.run(local_file_paths=[f.name], inputs={}, options={})

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Agent access_key/secret_key is not configured")
        self.assertEqual(FakeAsyncClient.calls, [])

    async def test_missing_app_id_fails_before_request(self) -> None:
        client = GenericAgentClient(base_url="http://ab", authorization="sk-test")

        result = await client.run(local_file_paths=[], inputs={}, options={})

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Agent app_id is not configured")
        self.assertEqual(FakeAsyncClient.calls, [])


if __name__ == "__main__":
    unittest.main()
