from __future__ import annotations

from fastapi.responses import JSONResponse

from ...dispatch import AgentScheduler
from ...services.agent_runtime import AgentContext
from .agent import KehutongSecretaryAgent
from .schema import KehutongSecretaryResp


async def run(ctx: AgentContext):
    scheduler = AgentScheduler(
        idempotency_ttl_sec=ctx.settings.IDEMPOTENCY_TTL_SEC,
        job_ttl_sec=ctx.settings.JOB_TTL_SEC,
    )
    agent = KehutongSecretaryAgent()
    agent.name = ctx.agent_name
    submit_result = await scheduler.submit(ctx=ctx, agent=agent)
    body = KehutongSecretaryResp(
        request_id=submit_result.request_id,
        status=submit_result.status,
        result=submit_result.result,
        error=submit_result.error,
    ).model_dump(exclude_none=True)
    if submit_result.http_status == 200:
        return KehutongSecretaryResp(**body)
    return JSONResponse(status_code=submit_result.http_status, content=body)
