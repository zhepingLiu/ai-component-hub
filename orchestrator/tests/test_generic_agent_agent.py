from __future__ import annotations

import unittest
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

if "app.config" in sys.modules and not hasattr(sys.modules["app.config"], "Settings"):
    sys.modules["app.config"].Settings = object

from app.agents.generic_agent import handler as handler_module
from app.agents.generic_agent.agent import GenericAgent
from app.agents.generic_agent.handler import run as run_handler
from app.agents.generic_agent.schema import GenericAgentReq
from app.schemas.common import AgentResult


class FakeLogger:
    def info(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class FakeTracker:
    def __init__(self) -> None:
        self.statuses: list[dict] = []

    async def aset_status(self, request_id: str, *, status: str, result, error, ttl: int) -> None:
        self.statuses.append(
            {
                "request_id": request_id,
                "status": status,
                "result": result,
                "error": error,
                "ttl": ttl,
            }
        )


@dataclass
class FakeClient:
    result: AgentResult

    async def run(self, *, local_file_paths, inputs, options):
        return self.result


class GenericAgentExecutionTests(unittest.IsolatedAsyncioTestCase):
    def _ctx(self, tracker: FakeTracker) -> SimpleNamespace:
        return SimpleNamespace(
            request_id="req-1",
            trace_id="trace-1",
            agent_name="some-agent",
            agent_config={"callback_url": "http://callback", "headers": {"channel": "ch"}},
            settings=SimpleNamespace(
                JOB_TTL_SEC=60,
                DOC_OCR_CALLBACK_TIMEOUT_SEC=1.0,
                DOC_OCR_CALLBACK_MAX_RETRIES=1,
                DOC_OCR_CALLBACK_BASE_DELAY_SEC=0.0,
                STAGING_DIR="/tmp",
                STAGING_DOWNLOAD_TIMEOUT_SEC=1.0,
            ),
            tracker=tracker,
            logger=FakeLogger(),
        )

    async def test_execute_succeeds_and_updates_status(self) -> None:
        tracker = FakeTracker()
        ctx = self._ctx(tracker)
        agent = GenericAgent()
        req = GenericAgentReq.model_validate({"inputs": {"q": "hello"}})

        with patch(
            "app.agents.generic_agent.agent.build_generic_agent_client",
            return_value=FakeClient(AgentResult(ok=True, data={"answer": "ok"})),
        ):
            result = await agent.execute(ctx, req)

        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual([item["status"] for item in tracker.statuses], ["RUNNING", "SUCCEEDED"])
        self.assertEqual(tracker.statuses[-1]["result"]["agent"], {"answer": "ok"})

    async def test_execute_failure_updates_status(self) -> None:
        tracker = FakeTracker()
        ctx = self._ctx(tracker)
        agent = GenericAgent()
        req = GenericAgentReq.model_validate({})

        with patch(
            "app.agents.generic_agent.agent.build_generic_agent_client",
            return_value=FakeClient(AgentResult(ok=False, data={}, error="boom")),
        ):
            result = await agent.execute(ctx, req)

        self.assertEqual(result.status, "FAILED")
        self.assertEqual([item["status"] for item in tracker.statuses], ["RUNNING", "FAILED"])
        self.assertEqual(tracker.statuses[-1]["error"], "boom")

    async def test_postprocess_sends_json_callback(self) -> None:
        tracker = FakeTracker()
        ctx = self._ctx(tracker)
        agent = GenericAgent()
        callback = AsyncMock(return_value={"status": "OK", "attempts": 1, "error": None})

        with patch("app.agents.generic_agent.agent.send_generic_agent_callback", callback):
            result = await agent.postprocess(
                ctx,
                GenericAgentReq.model_validate({}),
                SimpleNamespace(status="SUCCEEDED", data={"agent": {"ok": True}}, error=None),
            )

        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(result.data["callback"]["status"], "OK")
        callback.assert_awaited_once()
        self.assertEqual(callback.await_args.kwargs["status"], "SUCCEEDED")
        self.assertEqual(callback.await_args.kwargs["result"], {"agent": {"ok": True}})

    async def test_handler_returns_queued_submit_response(self) -> None:
        class FakeScheduler:
            def __init__(self, *args, **kwargs):
                return None

            async def submit(self, *, ctx, agent):
                return SimpleNamespace(
                    request_id="req-1",
                    status="QUEUED",
                    result=None,
                    error=None,
                    http_status=202,
                )

        ctx = SimpleNamespace(settings=SimpleNamespace(IDEMPOTENCY_TTL_SEC=60, JOB_TTL_SEC=60), agent_name="some-agent")

        with patch.object(handler_module, "AgentScheduler", FakeScheduler):
            response = await run_handler(ctx)

        self.assertEqual(response.status_code, 202)


if __name__ == "__main__":
    unittest.main()
