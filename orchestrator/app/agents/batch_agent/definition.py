from __future__ import annotations

from abc import abstractmethod
from typing import Any

from ...agent_registry import load_agent_configs
from ...batch.definition import BaseBatchDefinition, BatchContext
from ...core.context import AgentExecutionContext
from .schema import BatchAgentEnvelope


class BatchAgentDefinition(BaseBatchDefinition):
    """Durable definition base that completes the owning BaseAgent request."""

    @abstractmethod
    async def validate_business_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    async def validate_input(self, batch_input: dict[str, Any]) -> dict[str, Any]:
        envelope = BatchAgentEnvelope.model_validate(batch_input)
        envelope.payload = await self.validate_business_payload(envelope.payload)
        return envelope.model_dump()

    def envelope(self, ctx: BatchContext) -> BatchAgentEnvelope:
        return BatchAgentEnvelope.model_validate(ctx.batch.input)

    def agent_config(self, ctx: BatchContext) -> dict[str, Any]:
        envelope = self.envelope(ctx)
        return load_agent_configs(ctx.settings.AGENT_CONFIG_FILE).get(envelope.agent_name, {})

    async def on_batch_finished(self, ctx: BatchContext) -> None:
        envelope = self.envelope(ctx)
        result = dict(ctx.batch.result or {})
        failed = ctx.batch.failed > 0 and envelope.fail_on_any_task_error
        status = "FAILED" if failed else "SUCCEEDED"
        error = f"{ctx.batch.failed} batch task(s) failed" if failed else None
        await self._complete_agent_request(ctx, status=status, result=result, error=error)

    async def on_batch_failed(self, ctx: BatchContext, error: str) -> None:
        await self._complete_agent_request(ctx, status="FAILED", result=ctx.batch.result, error=error)

    async def _complete_agent_request(
        self,
        ctx: BatchContext,
        *,
        status: str,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        envelope = self.envelope(ctx)
        config = self.agent_config(ctx)
        from ...redis_client import create_redis_client
        from ...services.job_tracker import JobTracker
        from ..generic_agent.callback import send_generic_agent_callback

        tracker = JobTracker(create_redis_client())
        callback_result = await send_generic_agent_callback(
            ctx=AgentExecutionContext(
                request_id=envelope.request_id,
                trace_id=envelope.trace_id,
                agent_name=envelope.agent_name,
                agent_config=config,
                settings=ctx.settings,
                tracker=tracker,
                logger=ctx.logger,
            ),
            callback_url=str(config.get("callback_url", "")),
            status=status,
            result=result,
            error=error,
            timeout=self._number(config, "callback_timeout_sec", 10.0),
            max_retries=int(self._number(config, "callback_max_retries", 5)),
            base_delay=self._number(config, "callback_base_delay_sec", 1.0),
        )
        delivered = dict(result or {})
        delivered["batch_id"] = ctx.batch.batch_id
        delivered["callback"] = callback_result
        await tracker.aset_status(
            envelope.request_id,
            status=status,
            result=delivered,
            error=error,
            ttl=ctx.settings.JOB_TTL_SEC,
        )

    @staticmethod
    def _number(config: dict[str, Any], key: str, default: float) -> float:
        try:
            return float(config.get(key, default))
        except (TypeError, ValueError):
            return default
