from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from ..core.agent import BaseAgent
from ..core.context import AgentExecutionContext
from ..core.result import AgentExecutionResult, AgentSubmitResult
from ..services.agent_runtime import AgentContext


class AgentScheduler:
    def __init__(
        self,
        *,
        idempotency_ttl_sec: int,
        job_ttl_sec: int,
    ):
        self.idempotency_ttl_sec = idempotency_ttl_sec
        self.job_ttl_sec = job_ttl_sec

    async def submit(self, *, ctx: AgentContext, agent: BaseAgent) -> AgentSubmitResult:
        if ctx.json_body is None:
            raise HTTPException(status_code=400, detail="invalid_json")

        try:
            req = await agent.validate_request(ctx.json_body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        tracker = ctx.tracker
        request_id = ctx.request_id
        trace_id = ctx.request.headers.get("X-Trace-Id")
        logger = ctx.logger
        event_prefix = agent.log_event_prefix or agent.name

        logger.info({"event": f"{event_prefix}.received", "request_id": request_id, "trace_id": trace_id})
        logger.info({"event": f"{event_prefix}.check_existing", "request_id": request_id})
        _, existing = await tracker.aget_job(request_id)
        if existing:
            return AgentSubmitResult(
                request_id=request_id,
                status=existing.get("status", "UNKNOWN"),
                result=existing.get("result"),
                error=existing.get("error"),
            )

        logger.info(
            {"event": f"{event_prefix}.acquire_lock", "request_id": request_id, "ttl": self.idempotency_ttl_sec}
        )
        token, _ = await tracker.aacquire_lock(request_id, ttl=self.idempotency_ttl_sec)
        if not token:
            logger.info({"event": f"{event_prefix}.lock_busy", "request_id": request_id})
            return AgentSubmitResult(request_id=request_id, status="RUNNING")

        await tracker.aset_status(request_id, status="QUEUED", result=None, error=None, ttl=self.job_ttl_sec)

        queue = self._get_queue(ctx, agent.name)

        def _job_factory():
            return self._run_job(ctx=ctx, agent=agent, req=req, lock_token=token)

        accepted = queue.try_enqueue_nowait(request_id, _job_factory)
        if not accepted:
            error = "queue_full"
            await tracker.aset_status(request_id, status="REJECTED", result=None, error=error, ttl=self.job_ttl_sec)
            await tracker.arelease_lock(request_id, token)
            logger.warning({"event": f"{event_prefix}.rejected_queue_full", "request_id": request_id, "trace_id": trace_id})
            return AgentSubmitResult(request_id=request_id, status="REJECTED", http_status=429, error=error)

        logger.info({"event": f"{event_prefix}.queued", "request_id": request_id, "trace_id": trace_id})
        return AgentSubmitResult(request_id=request_id, status="QUEUED", http_status=202)

    async def _run_job(self, *, ctx: AgentContext, agent: BaseAgent, req: Any, lock_token: str) -> None:
        exec_ctx = AgentExecutionContext(
            request_id=ctx.request_id,
            trace_id=ctx.request.headers.get("X-Trace-Id"),
            agent_name=ctx.agent_name,
            agent_config=ctx.agent_config,
            settings=ctx.settings,
            tracker=ctx.tracker,
            logger=ctx.logger,
        )

        result: AgentExecutionResult
        try:
            result = await agent.execute(exec_ctx, req)
        except Exception as exc:
            event_prefix = agent.log_event_prefix or agent.name
            result = AgentExecutionResult.failed(str(exc))
            await ctx.tracker.aset_status(
                ctx.request_id,
                status="FAILED",
                result=None,
                error=result.error,
                ttl=self.job_ttl_sec,
            )
            ctx.logger.exception(
                {"event": f"{event_prefix}.unhandled_failed", "request_id": ctx.request_id, "error": result.error}
            )

        try:
            result = await agent.postprocess(exec_ctx, req, result)
            if result.status == "SUCCEEDED":
                await ctx.tracker.aset_status(
                    ctx.request_id,
                    status=result.status,
                    result=result.data,
                    error=result.error,
                    ttl=self.job_ttl_sec,
                )
        finally:
            await ctx.tracker.arelease_lock(ctx.request_id, lock_token)

    def _get_queue(self, ctx: AgentContext, agent_name: str):
        queues = getattr(ctx.request.app.state, "agent_queues", {}) or {}
        queue = queues.get(agent_name)
        if queue is not None:
            return queue
        legacy_name = f"{agent_name.replace('-', '_')}_queue"
        queue = getattr(ctx.request.app.state, legacy_name, None)
        if queue is not None:
            return queue
        raise RuntimeError(f"Queue is not configured for agent: {agent_name}")
