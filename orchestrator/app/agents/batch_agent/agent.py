from __future__ import annotations

from abc import abstractmethod
from datetime import UTC, datetime
from typing import Any

from ...batch.models import BatchOptions
from ...batch.service import BatchService
from ...batch.store import RedisBatchStore
from ...core.agent import BaseAgent
from ...core.context import AgentExecutionContext
from ...core.result import AgentExecutionResult
from .schema import BatchAgentEnvelope


class BatchAgent(BaseAgent):
    """Base skeleton for agents whose single request becomes a durable batch."""

    batch_type: str

    @abstractmethod
    async def build_batch_payload(self, ctx: AgentExecutionContext, req: Any) -> dict[str, Any]:
        """Build the business payload persisted with the batch."""

    def get_batch_options(self, req: Any) -> BatchOptions:
        value = getattr(getattr(req, "options", None), "batch", None)
        return value.model_copy(deep=True) if isinstance(value, BatchOptions) else BatchOptions()

    def get_scheduled_at(self, req: Any) -> datetime | None:
        return getattr(getattr(req, "options", None), "scheduled_at", None)

    def fail_on_any_task_error(self, req: Any) -> bool:
        return bool(getattr(getattr(req, "options", None), "fail_on_any_task_error", False))

    async def execute(self, ctx: AgentExecutionContext, req: Any) -> AgentExecutionResult:
        redis_client = getattr(ctx.tracker, "r", None)
        if redis_client is None:
            raise RuntimeError("durable batch agents require a Redis-backed JobTracker")

        from ...batch.definitions import build_batch_registry

        store = RedisBatchStore(
            redis_client,
            key_prefix=ctx.settings.BATCH_REDIS_KEY_PREFIX,
            retention_seconds=ctx.settings.BATCH_RETENTION_SEC,
        )
        service = BatchService(store, build_batch_registry())
        envelope = BatchAgentEnvelope(
            request_id=ctx.request_id,
            trace_id=ctx.trace_id,
            agent_name=ctx.agent_name,
            fail_on_any_task_error=self.fail_on_any_task_error(req),
            payload=await self.build_batch_payload(ctx, req),
        )
        batch, _ = await service.create_batch(
            batch_type=self.batch_type,
            batch_input=envelope.model_dump(),
            options=self.get_batch_options(req),
            idempotency_key=f"agent:{ctx.agent_name}:{ctx.request_id}",
            scheduled_at=self.get_scheduled_at(req),
        )
        result = {"batch_id": batch.batch_id, "batch_status": str(batch.status)}
        scheduled_at = self.get_scheduled_at(req)
        seconds_until_start = 0
        if scheduled_at is not None:
            seconds_until_start = max(0, int((scheduled_at - datetime.now(UTC)).total_seconds()))
        tracker_ttl = max(
            ctx.settings.JOB_TTL_SEC,
            ctx.settings.BATCH_RETENTION_SEC + seconds_until_start,
        )
        agent_status = "SCHEDULED" if str(batch.status) == "SCHEDULED" else "RUNNING"
        await ctx.tracker.aset_status(
            ctx.request_id,
            status=agent_status,
            result=result,
            error=None,
            ttl=tracker_ttl,
        )
        return AgentExecutionResult(status=agent_status, data=result)

    async def postprocess(
        self,
        ctx: AgentExecutionContext,
        req: Any,
        result: AgentExecutionResult,
    ) -> AgentExecutionResult:
        # Final delivery belongs to the durable definition's finalize phase.
        return result
