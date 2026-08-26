from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import asyncio
import httpx
from pydantic import BaseModel, Field

from ..agent_registry import load_agent_configs
from ..config import settings
from .definition import BaseBatchDefinition, BatchContext, BatchTaskSpec
from .models import TaskRecord
from .registry import BatchDefinitionRegistry
from .results import TaskExecutionResult


class EchoBatchInput(BaseModel):
    items: list[Any] = Field(default_factory=list)
    fail_business_keys: list[str] = Field(default_factory=list)


class EchoBatchDefinition(BaseBatchDefinition):
    """Built-in smoke-test definition for validating the batch kernel."""

    batch_type = "echo_batch"
    version = "1"

    async def validate_input(self, batch_input: dict[str, Any]) -> dict[str, Any]:
        return EchoBatchInput.model_validate(batch_input).model_dump()

    async def produce_tasks(
        self, ctx: BatchContext, batch_input: dict[str, Any], runtime_context: dict[str, Any]
    ) -> AsyncIterator[BatchTaskSpec]:
        data = EchoBatchInput.model_validate(batch_input)
        for index, item in enumerate(data.items):
            business_key = str(item.get("id")) if isinstance(item, dict) and item.get("id") is not None else str(index)
            yield BatchTaskSpec(business_key=business_key, payload={"item": item})

    async def execute_task(
        self, ctx: BatchContext, task: TaskRecord, runtime_context: dict[str, Any]
    ) -> TaskExecutionResult:
        failures = set(ctx.batch.input.get("fail_business_keys", []))
        if task.business_key in failures:
            return TaskExecutionResult.permanent_failure("ECHO_REJECTED", "configured echo failure")
        return TaskExecutionResult.succeeded({"echo": task.payload.get("item")})

    async def finalize(self, ctx: BatchContext) -> dict[str, Any]:
        return {
            "total": ctx.batch.total,
            "succeeded": ctx.batch.succeeded,
            "failed": ctx.batch.failed,
        }


class AgentBatchItem(BaseModel):
    business_key: str = Field(min_length=1, max_length=500)
    request: dict[str, Any] = Field(default_factory=dict)


class AgentBatchInput(BaseModel):
    agent_name: str = Field(min_length=1, max_length=100)
    items: list[AgentBatchItem] = Field(default_factory=list)
    poll_interval_seconds: float = Field(default=1.0, ge=0.2, le=30.0)


class AgentBatchDefinition(BaseBatchDefinition):
    """Fan out opaque requests to any agent registered in agents.yaml."""

    batch_type = "agent_batch"
    version = "1"

    async def validate_input(self, batch_input: dict[str, Any]) -> dict[str, Any]:
        data = AgentBatchInput.model_validate(batch_input)
        agent_configs = load_agent_configs(settings.AGENT_CONFIG_FILE)
        if data.agent_name not in agent_configs:
            raise ValueError(f"agent is not registered: {data.agent_name}")
        return data.model_dump()

    async def prepare(self, ctx: BatchContext, batch_input: dict[str, Any]) -> dict[str, Any]:
        data = AgentBatchInput.model_validate(batch_input)
        base_url = (ctx.settings.BATCH_AGENT_BASE_URL or ctx.settings.ORCHESTRATOR_BASE_URL).rstrip("/")
        if not base_url:
            raise ValueError("BATCH_AGENT_BASE_URL or ORCHESTRATOR_BASE_URL must be configured")
        return {
            "agent_name": data.agent_name,
            "agent_url": f"{base_url}/agents/{quote(data.agent_name, safe='')}",
            "poll_interval_seconds": data.poll_interval_seconds,
        }

    async def produce_tasks(
        self, ctx: BatchContext, batch_input: dict[str, Any], runtime_context: dict[str, Any]
    ) -> AsyncIterator[BatchTaskSpec]:
        data = AgentBatchInput.model_validate(batch_input)
        for item in data.items:
            yield BatchTaskSpec(
                business_key=item.business_key,
                payload={"request": item.request},
            )

    async def execute_task(
        self, ctx: BatchContext, task: TaskRecord, runtime_context: dict[str, Any]
    ) -> TaskExecutionResult:
        agent_name = str(runtime_context["agent_name"])
        agent_url = str(runtime_context["agent_url"])
        poll_interval = float(runtime_context.get("poll_interval_seconds", 1.0))
        request_id = self._agent_request_id(ctx, task)
        request_payload = dict(task.payload.get("request") or {})
        request_payload["request_id"] = request_id
        headers = {"X-Trace-Id": ctx.batch.batch_id}

        try:
            async with httpx.AsyncClient(timeout=ctx.settings.REQUEST_TIMEOUT_SEC) as client:
                response = await client.post(agent_url, json=request_payload, headers=headers)
                immediate = self._parse_response(response)
                if isinstance(immediate, TaskExecutionResult):
                    return immediate
                status_payload = immediate
                while True:
                    result = self._status_result(agent_name, request_id, status_payload)
                    if result is not None:
                        return result
                    await asyncio.sleep(poll_interval)
                    response = await client.get(
                        agent_url,
                        params={"request_id": request_id},
                        headers=headers,
                    )
                    parsed = self._parse_response(response)
                    if isinstance(parsed, TaskExecutionResult):
                        return parsed
                    status_payload = parsed
        except httpx.TimeoutException as exc:
            return TaskExecutionResult.retryable_failure("AGENT_TIMEOUT", str(exc))
        except httpx.RequestError as exc:
            return TaskExecutionResult.retryable_failure("AGENT_TRANSPORT_ERROR", str(exc))

    def _parse_response(self, response: httpx.Response) -> dict[str, Any] | TaskExecutionResult:
        if response.status_code == 422:
            return TaskExecutionResult.permanent_failure("AGENT_REQUEST_INVALID", response.text)
        if response.status_code == 404:
            return TaskExecutionResult.permanent_failure("AGENT_NOT_FOUND", response.text)
        if response.status_code == 429 or response.status_code >= 500:
            return TaskExecutionResult.retryable_failure(
                "AGENT_UNAVAILABLE", f"agent returned HTTP {response.status_code}: {response.text}"
            )
        if response.status_code >= 400:
            return TaskExecutionResult.permanent_failure(
                "AGENT_HTTP_ERROR", f"agent returned HTTP {response.status_code}: {response.text}"
            )
        try:
            payload = response.json()
        except ValueError:
            return TaskExecutionResult.retryable_failure("AGENT_INVALID_RESPONSE", response.text)
        if not isinstance(payload, dict):
            return TaskExecutionResult.retryable_failure("AGENT_INVALID_RESPONSE", str(payload))
        return payload

    def _status_result(
        self, agent_name: str, request_id: str, payload: dict[str, Any]
    ) -> TaskExecutionResult | None:
        status = str(payload.get("status", "UNKNOWN")).upper()
        if status == "SUCCEEDED":
            return TaskExecutionResult.succeeded(
                {
                    "agent_name": agent_name,
                    "agent_request_id": request_id,
                    "result": payload.get("result"),
                }
            )
        if status == "FAILED":
            return TaskExecutionResult.retryable_failure(
                "AGENT_FAILED", str(payload.get("error") or "agent execution failed")
            )
        if status == "REJECTED":
            return TaskExecutionResult.retryable_failure(
                "AGENT_REJECTED", str(payload.get("error") or "agent request was rejected")
            )
        return None

    def _agent_request_id(self, ctx: BatchContext, task: TaskRecord) -> str:
        base = f"{ctx.batch.batch_id}:{task.business_key}"
        if task.error_code in {"AGENT_FAILED", "AGENT_REJECTED"}:
            return f"{base}:attempt-{task.attempt}"
        return base

    async def finalize(self, ctx: BatchContext) -> dict[str, Any]:
        return {
            "agent_name": ctx.batch.input.get("agent_name"),
            "total": ctx.batch.total,
            "succeeded": ctx.batch.succeeded,
            "failed": ctx.batch.failed,
        }


def build_batch_registry() -> BatchDefinitionRegistry:
    from ..agents.kehutong_secretary.definition import KehutongSecretaryBatchDefinition

    registry = BatchDefinitionRegistry()
    registry.register(EchoBatchDefinition())
    registry.register(AgentBatchDefinition())
    registry.register(KehutongSecretaryBatchDefinition())
    return registry
