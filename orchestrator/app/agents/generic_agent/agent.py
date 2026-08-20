from __future__ import annotations

from typing import Any

from ...core.agent import BaseAgent
from ...core.context import AgentExecutionContext
from ...core.result import AgentExecutionResult
from .callback import send_generic_agent_callback
from .schema import GenericAgentReq
from .workflow import build_generic_agent_client, stage_generic_agent_files


class GenericAgent(BaseAgent):
    name = "generic-agent"
    log_event_prefix = "generic_agent"

    async def validate_request(self, payload: dict[str, Any]) -> GenericAgentReq:
        return GenericAgentReq.model_validate(payload)

    async def execute(self, ctx: AgentExecutionContext, req: GenericAgentReq) -> AgentExecutionResult:
        cfg = ctx.settings
        event_prefix = "generic_agent"

        try:
            await ctx.tracker.aset_status(
                ctx.request_id,
                status="RUNNING",
                result=None,
                error=None,
                ttl=cfg.JOB_TTL_SEC,
            )
            ctx.logger.info(
                {
                    "event": f"{event_prefix}.running",
                    "agent": ctx.agent_name,
                    "request_id": ctx.request_id,
                    "trace_id": ctx.trace_id,
                }
            )

            staged_files = await stage_generic_agent_files(ctx=ctx, req=req) if req.files else []
            local_paths = [staged.local_path for staged in staged_files]
            client = build_generic_agent_client(ctx.agent_config or {})
            agent_res = await client.run(local_file_paths=local_paths, inputs=req.inputs, options=req.options)

            if not agent_res.ok:
                error = agent_res.error or "agent upstream error"
                await ctx.tracker.aset_status(
                    ctx.request_id,
                    status="FAILED",
                    result=None,
                    error=error,
                    ttl=cfg.JOB_TTL_SEC,
                )
                ctx.logger.error(
                    {
                        "event": f"{event_prefix}.failed",
                        "agent": ctx.agent_name,
                        "request_id": ctx.request_id,
                        "trace_id": ctx.trace_id,
                        "error": error,
                    }
                )
                return AgentExecutionResult.failed(error)

            result: dict[str, Any] = {"agent": agent_res.data}
            if staged_files:
                result["staged"] = [
                    {
                        "url": staged.url,
                        "local_path": staged.local_path,
                        "size_bytes": staged.size_bytes,
                        "sha256": staged.sha256,
                    }
                    for staged in staged_files
                ]

            await ctx.tracker.aset_status(
                ctx.request_id,
                status="SUCCEEDED",
                result=result,
                error=None,
                ttl=cfg.JOB_TTL_SEC,
            )
            ctx.logger.info(
                {
                    "event": f"{event_prefix}.succeeded",
                    "agent": ctx.agent_name,
                    "request_id": ctx.request_id,
                    "trace_id": ctx.trace_id,
                }
            )
            return AgentExecutionResult.succeeded(result)
        except Exception as exc:
            error = str(exc)
            await ctx.tracker.aset_status(
                ctx.request_id,
                status="FAILED",
                result=None,
                error=error,
                ttl=cfg.JOB_TTL_SEC,
            )
            ctx.logger.exception(
                {
                    "event": f"{event_prefix}.unhandled_failed",
                    "agent": ctx.agent_name,
                    "request_id": ctx.request_id,
                    "error": error,
                }
            )
            return AgentExecutionResult.failed(error)

    async def postprocess(
        self,
        ctx: AgentExecutionContext,
        req: GenericAgentReq,
        result: AgentExecutionResult,
    ) -> AgentExecutionResult:
        callback_url = self._cfg(ctx.agent_config, "callback_url")
        callback_info = await send_generic_agent_callback(
            ctx=ctx,
            callback_url=callback_url,
            status=result.status,
            result=result.data,
            error=result.error,
            timeout=self._float_cfg(
                ctx.agent_config,
                "callback_timeout_sec",
                default=ctx.settings.DOC_OCR_CALLBACK_TIMEOUT_SEC,
            ),
            max_retries=self._int_cfg(
                ctx.agent_config,
                "callback_max_retries",
                default=ctx.settings.DOC_OCR_CALLBACK_MAX_RETRIES,
            ),
            base_delay=self._float_cfg(
                ctx.agent_config,
                "callback_base_delay_sec",
                default=ctx.settings.DOC_OCR_CALLBACK_BASE_DELAY_SEC,
            ),
        )
        if result.status != "SUCCEEDED" or not isinstance(result.data, dict):
            return result

        data = dict(result.data)
        data["callback"] = callback_info
        return AgentExecutionResult.succeeded(data)

    def _cfg(self, agent_cfg: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = agent_cfg.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def _float_cfg(self, agent_cfg: dict[str, Any], key: str, *, default: float) -> float:
        try:
            return float(agent_cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def _int_cfg(self, agent_cfg: dict[str, Any], key: str, *, default: int) -> int:
        try:
            return int(agent_cfg.get(key, default))
        except (TypeError, ValueError):
            return default
