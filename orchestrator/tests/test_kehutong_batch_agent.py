from __future__ import annotations

import sys
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

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
from app.agents.kehutong_secretary import definition as definition_module
from app.agents.kehutong_secretary import result_delivery as delivery_module
from app.agents.kehutong_secretary.client import HttpCustomerDataProvider
from app.agents.kehutong_secretary.definition import KehutongSecretaryBatchDefinition
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
        if isinstance(self.responses, dict):
            return self.responses[url].pop(0)
        return self.responses.pop(0)

    async def get(self, url, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        if isinstance(self.responses, dict):
            return self.responses[url].pop(0)
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

    def test_minimal_request_defaults_options_and_business_date(self):
        req = KehutongSecretaryReq.model_validate({"request_id": "req-minimal"})

        self.assertEqual(req.options.batch, BatchOptions())
        self.assertEqual(req.inputs.customer_list_request.last_cust_no, "0")
        self.assertEqual(req.inputs.customer_list_request.pageSize, 50)
        self.assertEqual(
            req.inputs.customer_data_request.dataDate,
            datetime.now(ZoneInfo("Asia/Shanghai")).date(),
        )

    async def test_http_provider_reads_one_customer_page_and_aggregates_customer_data(self):
        config = CustomerProviderConfig.model_validate(
            {
                "headers": {"X-Api-Key": "secret"},
                "customer_list_api": {
                    "url": "http://customer/list",
                    "method": "POST",
                },
                "chat_messages_api": {
                    "url": "http://customer/chat-messages",
                    "method": "POST",
                },
                "follow_plan_api": {
                    "url": "http://customer/follow-plan",
                    "method": "POST",
                },
            }
        )
        provider = HttpCustomerDataProvider(config)
        FakeAsyncClient.responses = {
            "http://customer/list": [
                FakeResponse(
                    {
                        "data": [{"cust_no": 1001}, {"cust_no": "1002"}],
                        "stateCode": "0000",
                        "message": "success",
                    }
                )
            ],
            "http://customer/follow-plan": [
                FakeResponse(
                    {
                        "data": [{"title": "call customer", "target_type": "customer"}],
                        "stateCode": "0000",
                    }
                )
            ],
            "http://customer/chat-messages": [
                FakeResponse(
                    {
                        "data": {
                            "rows": [{"content": "first"}, {"content": "second"}],
                            "totalNum": 3,
                            "pageSize": 2000,
                            "pageNum": 1,
                        },
                        "stateCode": "0000",
                    }
                ),
                FakeResponse(
                    {
                        "data": {
                            "rows": [{"content": "third"}],
                            "totalNum": 3,
                            "pageSize": 2000,
                            "pageNum": 2,
                        },
                        "stateCode": "0000",
                    }
                ),
            ],
        }
        with patch.object(client_module.httpx, "AsyncClient", FakeAsyncClient):
            ids = await provider.list_customer_ids(
                {"last_cust_no": "1000", "pageSize": 50}
            )
            data = await provider.get_aggregate_data("1001", {"dataDate": "2026-08-26"})

        self.assertEqual(ids, ["1001", "1002"])
        self.assertEqual(
            data,
            {
                "follow_plan": [
                    {"title": "call customer", "target_type": "customer"}
                ],
                "msgs": {
                    "rows": [
                        {"content": "first"},
                        {"content": "second"},
                        {"content": "third"},
                    ],
                    "totalNum": 3,
                    "pageSize": 2000,
                    "pageNum": 1,
                },
            },
        )
        list_call = next(call for call in FakeAsyncClient.calls if call["url"].endswith("/list"))
        follow_call = next(
            call for call in FakeAsyncClient.calls if call["url"].endswith("/follow-plan")
        )
        chat_calls = [
            call for call in FakeAsyncClient.calls if call["url"].endswith("/chat-messages")
        ]
        self.assertEqual(
            list_call["json"],
            {
                "last_cust_no": "1000",
                "pageSize": 50,
            },
        )
        self.assertEqual(follow_call["json"], {"target_id": "1001"})
        self.assertEqual(len(chat_calls), 2)
        self.assertEqual(
            chat_calls[0]["json"],
            {
                "to_list": "1001",
                "from1": "1001",
                "data_dt": "20260826",
                "pageNum": 1,
                "pageSize": 2000,
            },
        )
        self.assertEqual(chat_calls[1]["json"]["pageNum"], 2)
        for call in (list_call, follow_call, *chat_calls):
            self.assertEqual(call["headers"]["channelSystem"], "AB")
            self.assertTrue(call["headers"]["traceId"])
        self.assertEqual(
            len({call["headers"]["traceId"] for call in (list_call, follow_call, *chat_calls)}),
            4,
        )
        self.assertNotIn("limit", list_call["json"])

    async def test_task_sends_aggregate_data_to_target_agent(self):
        aggregate_data = {
            "follow_plan": [{"title": "call customer"}],
            "msgs": {"rows": [{"content": "hello"}], "totalNum": 1},
        }
        provider = SimpleNamespace(get_aggregate_data=AsyncMock(return_value=aggregate_data))
        target_client = SimpleNamespace(
            run=AsyncMock(return_value=SimpleNamespace(ok=True, data={"answer": "done"}))
        )
        definition = KehutongSecretaryBatchDefinition()
        ctx = SimpleNamespace(batch=SimpleNamespace(batch_id="bat-1"))
        task = SimpleNamespace(payload={"customer_id": "1001"})
        runtime_context = {
            "customer_data_request": {"dataDate": "2026-08-26"},
            "agent_inputs": {},
            "agent_options": {},
        }

        with (
            patch.object(definition, "agent_config", return_value={"target_agent": {}}),
            patch.object(definition_module, "build_customer_provider", return_value=provider),
            patch(
                "app.agents.generic_agent.workflow.build_generic_agent_client",
                return_value=target_client,
            ),
        ):
            result = await definition.execute_task(ctx, task, runtime_context)

        self.assertEqual(result.output["result"], {"answer": "done"})
        provider.get_aggregate_data.assert_awaited_once_with(
            "1001", {"dataDate": "2026-08-26"}
        )
        agent_inputs = target_client.run.await_args.kwargs["inputs"]
        self.assertEqual(agent_inputs["customerId"], "1001")
        self.assertIsInstance(agent_inputs["aggregateData"], str)
        self.assertEqual(json.loads(agent_inputs["aggregateData"]), aggregate_data)
        self.assertNotIn("customerData", agent_inputs)

    async def test_agent_submission_creates_durable_batch_and_returns_running(self):
        req = KehutongSecretaryReq.model_validate(
            {
                "inputs": {
                    "customer_list_request": {
                        "last_cust_no": "1000000009",
                        "pageSize": 50,
                    }
                },
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
        self.assertEqual(
            kwargs["batch_input"]["payload"]["inputs"]["customer_list_request"],
            {"last_cust_no": "1000000009", "pageSize": 50},
        )
        self.assertEqual(
            kwargs["batch_input"]["payload"]["inputs"]["customer_data_request"]["dataDate"],
            datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        )
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
                    ESB_BASE_URL="http://file-server/results",
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
                        "result_subdir": None,
                    },
                )

            local_path = Path(upload.await_args.kwargs["local_file_path"])
            document = json.loads(local_path.read_text(encoding="utf-8"))

        self.assertEqual(len(document["customers"]), 2)
        self.assertEqual(document["customers"][0]["result"], {"answer": "A"})
        self.assertEqual(
            result["result_url"],
            "http://file-server/results/req_1-result.json",
        )
        self.assertEqual(result["server_file"], "req_1-result.json")
        self.assertNotIn("/None/", result["result_url"])
        self.assertEqual(
            upload.await_args.kwargs["server_path"],
            "http://file-server/results",
        )
        self.assertNotIn("customers", result)


if __name__ == "__main__":
    unittest.main()
