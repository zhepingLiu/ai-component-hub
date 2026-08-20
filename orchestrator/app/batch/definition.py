from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from .models import BatchOptions, BatchRecord, TaskRecord
from .results import TaskExecutionResult


@dataclass(frozen=True)
class BatchTaskSpec:
    business_key: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class BatchContext:
    batch: BatchRecord
    settings: Any
    logger: Any


class BaseBatchDefinition(ABC):
    batch_type: str
    version: str = "1"
    default_options = BatchOptions()

    @abstractmethod
    async def validate_input(self, batch_input: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize the opaque business input."""

    async def prepare(self, ctx: BatchContext, batch_input: dict[str, Any]) -> dict[str, Any]:
        return {}

    @abstractmethod
    async def produce_tasks(
        self, ctx: BatchContext, batch_input: dict[str, Any], runtime_context: dict[str, Any]
    ) -> AsyncIterator[BatchTaskSpec]:
        """Yield task specifications without loading the whole task set into memory."""
        if False:
            yield BatchTaskSpec("", {})

    @abstractmethod
    async def execute_task(
        self, ctx: BatchContext, task: TaskRecord, runtime_context: dict[str, Any]
    ) -> TaskExecutionResult:
        """Execute exactly one task. Retry policy belongs to the batch engine."""

    async def finalize(self, ctx: BatchContext) -> dict[str, Any] | None:
        return None

    async def cleanup(self, ctx: BatchContext) -> None:
        return None
