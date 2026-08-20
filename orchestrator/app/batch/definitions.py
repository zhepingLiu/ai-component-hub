from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

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


def build_batch_registry() -> BatchDefinitionRegistry:
    registry = BatchDefinitionRegistry()
    registry.register(EchoBatchDefinition())
    return registry
