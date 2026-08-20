from __future__ import annotations

from typing import Any

from ...core.context import AgentExecutionContext
from ...services.callbacks import send_callback


async def send_generic_agent_callback(
    *,
    ctx: AgentExecutionContext,
    callback_url: str,
    status: str,
    result: dict[str, Any] | None,
    error: str | None,
    timeout: float,
    max_retries: int,
    base_delay: float,
) -> dict[str, Any]:
    raw_headers = ctx.agent_config.get("headers", {}) or {}
    callback_headers = {str(k): str(v) for k, v in raw_headers.items() if v is not None and v != ""}
    callback_payload = {
        "request_id": ctx.request_id,
        "trace_id": ctx.trace_id or "",
        "agent": ctx.agent_name,
        "status": status,
        "result": result or {},
        "error": error or "",
    }
    return await send_callback(
        callback_url=callback_url,
        payload=callback_payload,
        content_type=None,
        headers=callback_headers or None,
        timeout=timeout,
        max_retries=max_retries,
        base_delay=base_delay,
        logger=ctx.logger,
        request_id=ctx.request_id,
        trace_id=ctx.trace_id,
    )
