from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

fake_config = ModuleType("app.config")
fake_config.Settings = object
fake_config.settings = SimpleNamespace(AB_ACCESS_KEY="", AB_SECRET_KEY="")
sys.modules.setdefault("app.config", fake_config)
fake_redis_client = ModuleType("app.redis_client")
fake_redis_client.create_redis_client = lambda: None
sys.modules.setdefault("app.redis_client", fake_redis_client)
fake_httpx = ModuleType("httpx")
fake_httpx.AsyncClient = object
fake_httpx.Response = object
fake_httpx.TimeoutException = type("TimeoutException", (Exception,), {})
fake_httpx.RequestError = type("RequestError", (Exception,), {})
sys.modules.setdefault("httpx", fake_httpx)
fake_yaml = ModuleType("yaml")
fake_yaml.safe_load = lambda value: {}
sys.modules.setdefault("yaml", fake_yaml)

from app.agents.batch_agent.agent import BatchAgent
from app.agents.kehutong_secretary.agent import KehutongSecretaryAgent
from app.agents.kehutong_secretary import client as client_module
from app.agents.kehutong_secretary import result_delivery as delivery_module
from app.agents.kehutong_secretary.client import HttpCustomerDataProvider
from app.agents.kehutong_secretary.schema import CustomerProviderConfig, KehutongSecretaryReq
from app.agents.kehutong_secretary.result_delivery import publish_kehutong_result
from app.batch.models import BatchOptions, BatchStatus, TaskStatus

for _module_name in ("app.config", "app.redis_client", "httpx", "yaml"):
    sys.modules.pop(_module_name, None)


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeAsyncClient:
    responses = []
    calls = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.responses.pop(0)

    async def get(self, url, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.responses.pop(0)


class FakeTracker:
    def __init__(self):
        self.r = object()
        self.statuses = []

    async def aset_status(self, request_id, **kwargs):
        self.statuses.append((request_id, kwargs))


class KehutongBatchAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeAsyncClient.responses = []
        FakeAsyncClient.calls = []

    def test_kehutong_agent_inherits_durable_batch_skeleton(self):
        self.assertTrue(issubclass(KehutongSecretaryAgent, BatchAgent))
        self.assertEqual(KehutongSecretaryAgent.batch_type, "kehutong_secretary")

    async def test_http_provider_reads_both_configured_apis(self):
        config = CustomerProviderConfig.model_validate(
            {
                "headers": {"X-Api-Key": "secret"},
                "customer_list_api": {
                    "url": "http://customer/list",
                    "method": "POST",
                    "static_body": {"status": "active"},
                    "response": {"customer_ids_path": "data.customerIds"},
                },
                "customer_data_api": {
                    "url": "http://customer/data",
                    "method": "POST",
                    "customer_id": {"location": "body", "field": "customerId"},
                    "response": {"customer_data_path": "data"},
                },
            }
        )
        provider = HttpCustomerDataProvider(config)
        FakeAsyncClient.responses = [
            FakeResponse({"data": {"customerIds": [1001, "1002"]}}),
            FakeResponse({"data": {"score": 88}}),
        ]
        with patch.object(client_module.httpx, "AsyncClient", FakeAsyncClient):
            ids = await provider.list_customer_ids({"segment": "A"})
            data = await provider.get_customer_data("1001", {"date": "2026-08-26"})

        self.assertEqual(ids, ["1001", "1002"])
        self.assertEqual(data, {"score": 88})
        self.assertEqual(FakeAsyncClient.calls[0]["json"]["status"], "active")
        self.assertEqual(FakeAsyncClient.calls[0]["json"]["segment"], "A")
        self.assertEqual(FakeAsyncClient.calls[1]["json"]["customerId"], "1001")

    async def test_agent_submission_creates_durable_batch_and_returns_running(self):
        req = KehutongSecretaryReq.model_validate(
            {
                "inputs": {"customer_list_request": {"segment": "A"}},
                "options": {
                    "batch": {"max_concurrency": 7, "max_attempts": 4},
                    "fail_on_any_task_error": True,
                },
            }
        )
        tracker = FakeTracker()
        ctx = SimpleNamespace(
            request_id="req-1",
            trace_id="trace-1",
            agent_name="kehutong_secretary",
            tracker=tracker,
            settings=SimpleNamespace(
                BATCH_REDIS_KEY_PREFIX="test:batch",
                BATCH_RETENTION_SEC=3600,
                JOB_TTL_SEC=3600,
            ),
        )
        batch = SimpleNamespace(batch_id="bat-1", status=BatchStatus.CREATED)
        with patch(
            "app.agents.batch_agent.agent.BatchService.create_batch",
            AsyncMock(return_value=(batch, True)),
        ) as create_batch:
            result = await KehutongSecretaryAgent().execute(ctx, req)

        self.assertEqual(result.status, "RUNNING")
        self.assertEqual(result.data["batch_id"], "bat-1")
        kwargs = create_batch.await_args.kwargs
        self.assertEqual(kwargs["batch_type"], "kehutong_secretary")
        self.assertEqual(kwargs["options"], BatchOptions(max_concurrency=7, max_attempts=4))
        self.assertEqual(kwargs["batch_input"]["request_id"], "req-1")
        self.assertTrue(kwargs["batch_input"]["fail_on_any_task_error"])
        self.assertEqual(tracker.statuses[-1][1]["status"], "RUNNING")

    async def test_finalize_publishes_one_json_and_returns_only_its_url_metadata(self):
        tasks = [
            SimpleNamespace(
                business_key="1001",
                status=TaskStatus.SUCCEEDED,
                attempt=1,
                output={"result": {"answer": "A"}},
                error=None,
            ),
            SimpleNamespace(
                business_key="1002",
                status=TaskStatus.FAILED,
                attempt=3,
                output=None,
                error="downstream unavailable",
            ),
        ]

        class FakeStore:
            def __init__(self, *args, **kwargs):
                pass

            async def list_tasks(self, batch_id, *, offset, limit):
                return tasks, None

        fake_redis = ModuleType("app.redis_client")
        fake_redis.create_redis_client = lambda: object()
        upload = AsyncMock(return_value=None)
        fake_file_stage = ModuleType("app.services.file_stage")
        fake_file_stage.upload_json_via_esb = upload

        with tempfile.TemporaryDirectory() as staging_dir:
            ctx = SimpleNamespace(
                batch=SimpleNamespace(
                    batch_id="bat-1",
                    total=2,
                    succeeded=1,
                    failed=1,
                    cancelled=0,
                ),
                settings=SimpleNamespace(
                    BATCH_REDIS_KEY_PREFIX="test:batch",
                    BATCH_RETENTION_SEC=3600,
                    STAGING_DIR=staging_dir,
                    ESB_UPLOAD_TIMEOUT_SEC=30,
                ),
            )
            with (
                patch.object(delivery_module, "RedisBatchStore", FakeStore),
                patch.dict(
                    sys.modules,
                    {
                        "app.redis_client": fake_redis,
                        "app.services.file_stage": fake_file_stage,
                    },
                ),
            ):
                result = await publish_kehutong_result(
                    ctx=ctx,
                    request_id="req:1",
                    agent_name="kehutong_secretary",
                    agent_config={
                        "result_server_path": "http://file-server/upload",
                        "result_public_base_url": "http://file-server/results",
                        "result_subdir": "kehutong_secretary",
                    },
                )

            local_path = Path(upload.await_args.kwargs["local_file_path"])
            document = json.loads(local_path.read_text(encoding="utf-8"))

        self.assertEqual(len(document["customers"]), 2)
        self.assertEqual(document["customers"][0]["result"], {"answer": "A"})
        self.assertEqual(
            result["result_url"],
            "http://file-server/results/kehutong_secretary/req_1-result.json",
        )
        self.assertNotIn("customers", result)


if __name__ == "__main__":
    unittest.main()
