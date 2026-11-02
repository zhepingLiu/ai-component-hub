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
from app.agents.kehutong_secretary.client import (
    CustomerPage, HttpCustomerDataProvider, PermanentCustomerApiError, RetryableCustomerApiError,
)
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
        self.assertEqual(req.inputs.customer_list_request.last_id, 0)
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
                "customer_relations_api": {"url": "http://customer/relations"},
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
                        "data": [{"cust_no": "1001", "id": 1001}, {"cust_no": "1002", "id": 1002}],
                        "stateCode": "0000",
                        "message": "success",
                    }
                ),
            ],
            "http://customer/relations": [FakeResponse({
                "stateCode": "0000", "data": [
                    {"customer_id": "1001", "emp_user_id": "manager-1"},
                    {"customer_id": "1001", "emp_user_id": "manager-1"},
                ],
            })],
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
                {"last_id": 1000, "pageSize": 50}
            )
            employees = await provider.list_employee_ids("1001")
            data = await provider.get_aggregate_data("1001", employees[0], {"dataDate": "2026-08-26"})

        self.assertEqual(ids, ["1001", "1002"])
        self.assertEqual(employees, ["manager-1"])
        relation_call = next(c for c in FakeAsyncClient.calls if c["url"].endswith("/relations"))
        self.assertEqual(relation_call["json"], {"customer_id": "1001"})
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
                "last_id": 1000,
                "pageSize": 50,
            },
        )
        self.assertEqual(follow_call["json"], {"target_id": "1001"})
        self.assertEqual(len(chat_calls), 2)
        self.assertEqual(
            chat_calls[0]["json"],
            {
                "to_list": "1001",
                "from1": "manager-1",
                "data_dt": "20260826",
                "pageNum": 1,
                "pageSize": 2000,
            },
        )
        self.assertEqual(chat_calls[1]["json"]["pageNum"], 2)
        for call in (list_call, relation_call, follow_call, *chat_calls):
            self.assertEqual(call["headers"]["channelSystem"], "AB")
            self.assertTrue(call["headers"]["traceId"])
        self.assertEqual(
            len({call["headers"]["traceId"] for call in (list_call, relation_call, follow_call, *chat_calls)}),
            5,
        )
        self.assertNotIn("limit", list_call["json"])

    async def test_customer_list_fetches_only_requested_page_and_preserves_customer_strings(self):
        config = CustomerProviderConfig.model_validate({
            name: {"url": "http://customer/" + name} for name in (
                "customer_list_api", "customer_relations_api", "chat_messages_api", "follow_plan_api",
            )
        })
        provider = HttpCustomerDataProvider(config)
        request = {"last_id": 100, "pageSize": 2}
        fetch = AsyncMock(side_effect=[
            {"data": [{"cust_no": "0099", "id": 9}, {"cust_no": "0001", "id": 4}]},
            {"data": [{"cust_no": "0002", "id": 15}]},
            {"data": []},
        ])
        with patch.object(provider, "_request", fetch):
            ids = await provider.list_customer_ids(request)
        self.assertEqual(ids, ["0099", "0001"])
        fetch.assert_awaited_once()
        self.assertEqual(fetch.await_args.args[1], {"last_id": 100, "pageSize": 2})
        self.assertEqual(request, {"last_id": 100, "pageSize": 2})

    async def test_customer_list_accepts_empty_page_and_records_with_cursor(self):
        config = CustomerProviderConfig.model_validate({
            name: {"url": "http://customer/" + name} for name in (
                "customer_list_api", "customer_relations_api", "chat_messages_api", "follow_plan_api",
            )
        })
        provider = HttpCustomerDataProvider(config)
        for records, expected in (([], []), ([{"cust_no": "0001", "id": 101}], ["0001"])):
            fetch = AsyncMock(return_value={"data": records})
            with self.subTest(records=records), patch.object(provider, "_request", fetch):
                self.assertEqual(await provider.list_customer_ids({"last_id": 100}), expected)
                fetch.assert_awaited_once()

    async def test_customer_list_rejects_bad_records(self):
        config = CustomerProviderConfig.model_validate({
            name: {"url": "http://customer/" + name} for name in (
                "customer_list_api", "customer_relations_api", "chat_messages_api", "follow_plan_api",
            )
        })
        provider = HttpCustomerDataProvider(config)
        for record in (
            None, {}, {"cust_no": 123, "id": 2},
            {"cust_no": "a"}, {"cust_no": "a", "id": True},
            {"cust_no": "a", "id": "2"}, {"cust_no": "a", "id": -1},
            {"cust_no": "", "id": 2}, {"cust_no": "   "},
        ):
            with self.subTest(record=record), patch.object(provider, "_request", AsyncMock(return_value={"data": [record]})):
                with self.assertRaises(PermanentCustomerApiError):
                    await provider.list_customer_ids({"last_id": 0})

    async def test_customer_page_cursor_uses_record_ids_and_preserves_empty_cursor(self):
        config = CustomerProviderConfig.model_validate({
            name: {"url": "http://customer/" + name} for name in (
                "customer_list_api", "customer_relations_api", "chat_messages_api", "follow_plan_api",
            )
        })
        provider = HttpCustomerDataProvider(config)
        for records, expected in (
            ([{"cust_no": "0099", "id": 105}, {"cust_no": "0099", "id": 109},
              {"cust_no": "0001", "id": 107}], 109),
            ([], 100),
        ):
            with self.subTest(records=records), patch.object(
                provider, "_request", AsyncMock(return_value={"data": records})
            ):
                page = await provider.list_customer_page({"last_id": 100})
                self.assertEqual(page.last_id, expected)
                self.assertEqual(page.customer_ids, [r["cust_no"] for r in records])

    async def test_agent_get_returns_persisted_cursor_on_success_and_failure(self):
        from app.routers import agent_runner
        from app.services.job_tracker import InMemoryJobTracker

        definition = KehutongSecretaryBatchDefinition()
        tracker = InMemoryJobTracker()
        envelope = SimpleNamespace(
            request_id="req-cursor", trace_id=None, agent_name="kehutong-secretary",
            fail_on_any_task_error=False,
        )
        ctx = SimpleNamespace(
            batch=SimpleNamespace(
                batch_id="bat-cursor", runtime_context={"last_id": 109},
                result={"summary": {"total": 2}}, failed=0,
            ),
            settings=SimpleNamespace(JOB_TTL_SEC=3600), logger=SimpleNamespace(),
        )
        request = SimpleNamespace(
            method="GET", query_params={"request_id": "req-cursor"},
            app=SimpleNamespace(state=SimpleNamespace(
                agent_configs={"kehutong-secretary": {}},
            )),
        )
        fake_redis = ModuleType("app.redis_client")
        fake_redis.create_redis_client = lambda: None
        with (
            patch.dict(sys.modules, {"app.redis_client": fake_redis}),
            patch.object(definition, "envelope", return_value=envelope),
            patch.object(definition, "agent_config", return_value={}),
            patch("app.services.job_tracker.JobTracker", return_value=tracker),
            patch("app.agents.generic_agent.callback.send_generic_agent_callback",
                  AsyncMock(return_value={"status": "SKIPPED"})),
            patch.object(agent_runner, "_get_tracker", return_value=tracker),
        ):
            await definition.on_batch_finished(ctx)
            response = await agent_runner.run_agent("kehutong-secretary", request)
            self.assertEqual(response.status, "SUCCEEDED")
            self.assertEqual(response.result["last_id"], 109)
            ctx.batch.result = None
            await definition.on_batch_failed(ctx, "upload failed")
            response = await agent_runner.run_agent("kehutong-secretary", request)
            self.assertEqual(response.status, "FAILED")
            self.assertEqual(response.result["last_id"], 109)
            self.assertEqual(response.error, "upload failed")

    def test_customer_cursor_requires_integer_and_rejects_obsolete_field(self):
        from pydantic import ValidationError
        for request in ({"last_id": "1"}, {"last_id": True}, {"last_id": -1}, {"last_cust_no": "1"}):
            with self.subTest(request=request), self.assertRaises(ValidationError):
                KehutongSecretaryReq.model_validate({"inputs": {"customer_list_request": request}})

    async def test_relations_reject_errors_and_malformed_data_but_allow_empty(self):
        config = CustomerProviderConfig.model_validate({
            name: {"url": "http://customer/" + name} for name in (
                "customer_list_api", "customer_relations_api", "chat_messages_api", "follow_plan_api",
            )
        })
        provider = HttpCustomerDataProvider(config)
        cases = [
            ({"stateCode": "9999", "data": []}, RetryableCustomerApiError),
            ({"data": []}, RetryableCustomerApiError),
            ({"stateCode": "0000", "data": {}}, PermanentCustomerApiError),
            ({"stateCode": "0000", "data": [{"customer_id": "other", "emp_user_id": "1"}]}, PermanentCustomerApiError),
            ({"stateCode": "0000", "data": [{"customer_id": "a", "emp_user_id": ""}]}, PermanentCustomerApiError),
            ({"stateCode": "0000", "data": [{"customer_id": "a"}]}, PermanentCustomerApiError),
        ]
        for response, error in cases:
            with self.subTest(response=response), patch.object(provider, "_request", AsyncMock(return_value=response)):
                with self.assertRaises(error):
                    await provider.list_employee_ids("a")
        with patch.object(provider, "_request", AsyncMock(return_value={"stateCode": "0000", "data": []})):
            self.assertEqual(await provider.list_employee_ids("a"), [])

    async def test_prepare_resolves_relations_before_generating_ordered_pair_tasks(self):
        definition = KehutongSecretaryBatchDefinition()
        ctx = SimpleNamespace()
        provider = SimpleNamespace(
            list_customer_page=AsyncMock(return_value=CustomerPage(["a", "a", "b", "c"], 123)),
            list_employee_ids=AsyncMock(side_effect=[["1", "2", "3"], [], ["1"]]),
        )
        with (
            patch.object(definition, "envelope", return_value=SimpleNamespace(payload={})),
            patch.object(definition, "agent_config", return_value={}),
            patch.object(definition_module, "build_customer_provider", return_value=provider),
        ):
            runtime = await definition.prepare(ctx, {})
        self.assertEqual(runtime["last_id"], 123)
        self.assertEqual([call.args for call in provider.list_employee_ids.await_args_list], [("a",), ("b",), ("c",)])
        specs = [spec async for spec in definition.produce_tasks(ctx, {}, runtime)]
        self.assertEqual([spec.payload for spec in specs], [
            {"customer_id": customer, "emp_user_id": employee}
            for customer, employee in [("a", "1"), ("a", "2"), ("a", "3"), ("c", "1")]
        ])
        self.assertEqual(len({spec.business_key for spec in specs}), 4)
        self.assertTrue(all(spec.wait_for_previous for spec in specs))

    def test_groups_per_request_requires_positive_integer(self):
        from pydantic import ValidationError
        self.assertEqual(KehutongSecretaryReq().options.groups_per_request, 1)
        for value in (0, -1, True, "3", 1.5):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                KehutongSecretaryReq.model_validate({"options": {"groups_per_request": value}})

    async def test_grouping_preserves_customer_order_and_splits_across_requests(self):
        definition = KehutongSecretaryBatchDefinition()
        provider = SimpleNamespace(
            list_customer_page=AsyncMock(return_value=CustomerPage(["a", "empty", "b"], 123)),
            list_employee_ids=AsyncMock(side_effect=[["1", "2"], [], ["1", "2", "3", "4", "5"]]),
        )
        with (
            patch.object(definition, "envelope", return_value=SimpleNamespace(
                payload={"options": {"groups_per_request": 3}})),
            patch.object(definition, "agent_config", return_value={}),
            patch.object(definition_module, "build_customer_provider", return_value=provider),
        ):
            runtime = await definition.prepare(SimpleNamespace(), {})
        specs = [spec async for spec in definition.produce_tasks(SimpleNamespace(), {}, runtime)]
        self.assertEqual([
            [(r["customer_id"], r["emp_user_id"]) for r in spec.payload["groups"]]
            for spec in specs
        ], [[("a", "1"), ("a", "2"), ("b", "1")],
            [("b", "2"), ("b", "3"), ("b", "4")], [("b", "5")]])
        self.assertEqual(len({spec.business_key for spec in specs}), 3)
        self.assertTrue(all(spec.wait_for_previous for spec in specs))
        runtime["customer_relations"] = []
        self.assertEqual([spec async for spec in definition.produce_tasks(SimpleNamespace(), {}, runtime)], [])

    async def test_grouped_task_sends_one_request_and_matches_reordered_results(self):
        definition = KehutongSecretaryBatchDefinition()
        relations = [{"customer_id": "a", "emp_user_id": "1"},
                     {"customer_id": "b", "emp_user_id": "2"}]
        for count in (1, 2):
            selected = relations[:count]
            answer = [{**r, "result": r["customer_id"]} for r in reversed(selected)]
            provider = SimpleNamespace(get_aggregate_data=AsyncMock(
                side_effect=[{"msgs": r["customer_id"]} for r in selected]))
            target = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
                ok=True, data={"answer": json.dumps(answer)})))
            task = SimpleNamespace(payload={"groups": selected}, business_key="group-key")
            with (
                patch.object(definition, "agent_config", return_value={}),
                patch.object(definition_module, "build_customer_provider", return_value=provider),
                patch("app.agents.generic_agent.workflow.build_generic_agent_client", return_value=target),
            ):
                result = await definition.execute_task(
                    SimpleNamespace(batch=SimpleNamespace(batch_id="bat-1")), task, {})
            target.run.assert_awaited_once()
            inputs = target.run.await_args.kwargs["inputs"]
            self.assertNotIn("customerId", inputs)
            self.assertEqual(json.loads(inputs["aggregateData"]), [
                {**r, "aggregate_data": {"msgs": r["customer_id"]}} for r in selected])
            self.assertEqual([r["result"]["answer"] for r in result.output["groups"]],
                             [r["customer_id"] for r in selected])

    def test_group_results_reject_missing_duplicate_or_unidentified_answers(self):
        relations = [{"customer_id": "a", "emp_user_id": "1"}]
        valid = {**relations[0], "result": "done"}
        for answer in ([], [valid, valid], [{**valid, "customer_id": "b"}],
                       [{"result": "done"}], "not JSON", {"result": "done"}):
            with self.subTest(answer=answer), self.assertRaises((ValueError, KeyError, TypeError)):
                KehutongSecretaryBatchDefinition.parse_group_results({"answer": answer}, relations)

    async def test_relation_failure_does_not_become_empty_success(self):
        definition = KehutongSecretaryBatchDefinition()
        provider = SimpleNamespace(
            list_customer_page=AsyncMock(return_value=CustomerPage(["a"], 123)),
            list_employee_ids=AsyncMock(side_effect=RetryableCustomerApiError("unavailable")),
        )
        with (
            patch.object(definition, "envelope", return_value=SimpleNamespace(payload={})),
            patch.object(definition, "agent_config", return_value={}),
            patch.object(definition_module, "build_customer_provider", return_value=provider),
        ):
            with self.assertRaises(RetryableCustomerApiError):
                await definition.prepare(SimpleNamespace(), {})

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
        task = SimpleNamespace(
            payload={"customer_id": "1001", "emp_user_id": "manager-1"},
            business_key='["1001","manager-1"]',
        )
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
            "1001", "manager-1", {"dataDate": "2026-08-26"}
        )
        agent_inputs = target_client.run.await_args.kwargs["inputs"]
        self.assertEqual(agent_inputs["customerId"], "1001")
        self.assertEqual(agent_inputs["batchRequestId"], 'bat-1:["1001","manager-1"]')
        self.assertEqual(result.output["emp_user_id"], "manager-1")
        self.assertIsInstance(agent_inputs["aggregateData"], str)
        self.assertEqual(json.loads(agent_inputs["aggregateData"]), aggregate_data)
        self.assertNotIn("customerData", agent_inputs)

    async def test_agent_submission_creates_durable_batch_and_returns_running(self):
        req = KehutongSecretaryReq.model_validate(
            {
                "inputs": {
                    "customer_list_request": {
                        "last_id": 1000000009,
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
            {"last_id": 1000000009, "pageSize": 50, "cust_no": None},
        )
        self.assertEqual(
            kwargs["batch_input"]["payload"]["inputs"]["customer_data_request"]["dataDate"],
            datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        )
        self.assertEqual(tracker.statuses[-1][1]["status"], "RUNNING")

    async def test_finalize_publishes_one_json_and_returns_only_its_url_metadata(self):
        tasks = [
            SimpleNamespace(
                business_key='["1001","manager-1"]',
                payload={"customer_id": "1001", "emp_user_id": "manager-1"},
                status=TaskStatus.SUCCEEDED,
                attempt=1,
                output={"result": {"answer": "A", "internal": "not-published"}},
                error=None,
            ),
            SimpleNamespace(
                business_key='["1001","manager-2"]',
                payload={"customer_id": "1001", "emp_user_id": "manager-2"},
                status=TaskStatus.FAILED,
                attempt=3,
                output=None,
                error="downstream unavailable",
            ),
        ]

        tasks.append(SimpleNamespace(
            payload={"groups": [{"customer_id": "2001", "emp_user_id": "m1"},
                                {"customer_id": "2002", "emp_user_id": "m2"}]},
            status=TaskStatus.SUCCEEDED, attempt=1, error=None,
            output={"groups": [
                {"customer_id": "2002", "emp_user_id": "m2", "result": {"answer": "D"}},
                {"customer_id": "2001", "emp_user_id": "m1", "result": {"answer": "C"}},
            ]},
        ))
        tasks.append(SimpleNamespace(
            payload={"groups": [{"customer_id": "3001", "emp_user_id": "m3"}]},
            status=TaskStatus.FAILED, attempt=3, error="group failed", output=None,
        ))

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
                    runtime_context={"last_id": 123},
                    total=4,
                    succeeded=2,
                    failed=2,
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
                for config in ({}, {"result_subdir": None}, {"result_subdir": "None"}, {"result_subdir": "SSC"}):
                    with self.subTest(config=config):
                        result = await publish_kehutong_result(
                            ctx=ctx,
                            request_id="req:1",
                            agent_name="kehutong_secretary",
                            agent_config=config,
                        )
                        self.assertEqual(result["server_file"], "req_1-result.json")
                        self.assertEqual(result["result_url"], "http://file-server/results/SSC/req_1-result.json")
                        self.assertEqual(upload.await_args.kwargs["server_file"], "req_1-result.json")
                        self.assertEqual(upload.await_args.kwargs["server_path"], "http://file-server/results")

                for public_base in ("https://fserver.uat.cqrcb.com/SSC/", "https://downloads.example.com/custom"):
                    with self.subTest(public_base=public_base):
                        custom_result = await publish_kehutong_result(
                            ctx=ctx,
                            request_id="req:1",
                            agent_name="kehutong_secretary",
                            agent_config={"result_public_base_url": public_base},
                        )
                        self.assertEqual(custom_result["result_url"], f"{public_base.rstrip('/')}/req_1-result.json")
                        self.assertEqual(upload.await_args.kwargs["server_path"], "http://file-server/results")
                        self.assertEqual(upload.await_args.kwargs["server_file"], "req_1-result.json")

            local_path = Path(upload.await_args.kwargs["local_file_path"])
            document = json.loads(local_path.read_text(encoding="utf-8"))

        self.assertEqual(result["last_id"], 123)
        self.assertEqual(len(document["customers"]), 5)
        self.assertEqual([x["customer_id"] for x in document["customers"]], ["1001", "1001", "2001", "2002", "3001"])
        self.assertEqual([x["emp_user_id"] for x in document["customers"]], ["manager-1", "manager-2", "m1", "m2", "m3"])
        self.assertEqual(document["customers"][0]["result"], "A")
        self.assertEqual(document["customers"][2]["result"], "C")
        self.assertEqual(document["customers"][3]["result"], "D")
        self.assertEqual(document["customers"][4]["error"], "group failed")
        self.assertEqual(document["summary"]["total"], 4)
        self.assertEqual(
            result["result_url"],
            "http://file-server/results/SSC/req_1-result.json",
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
