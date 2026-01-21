from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from ..agent_registry import get_handler_name, load_agent_handler
from ..config import settings
from ..schemas.common import AgentStatusResp
from ..services.agent_runtime import AgentContext
from ..services.job_tracker import InMemoryJobTracker, JobTracker


router = APIRouter()
logger = logging.getLogger("orchestrator")


def _get_tracker(request: Request):
    r = request.app.state.redis  # type: ignore[attr-defined]
    return JobTracker(r) if r else InMemoryJobTracker()


@router.api_route("/agents/{name}", methods=["GET", "POST"])
async def run_agent(name: str, request: Request):
    agent_configs = getattr(request.app.state, "agent_configs", {}) or {}
    agent_cfg = agent_configs.get(name)
    if not isinstance(agent_cfg, dict):
        raise HTTPException(status_code=404, detail="agent_not_found")

    tracker = _get_tracker(request)

    if request.method == "GET":
        request_id = tracker.ensure_request_id(request.query_params.get("request_id"))
        _, existing = tracker.get_job(request_id)
        if existing:
            return AgentStatusResp(
                request_id=request_id,
                status=existing.get("status", "UNKNOWN"),
                result=existing.get("result"),
                error=existing.get("error"),
            )
        return AgentStatusResp(request_id=request_id, status="UNKNOWN", result=None, error=None)

    raw_body = await request.body()
    json_body = None
    if raw_body:
        try:
            json_body = json.loads(raw_body.decode("utf-8"))
        except Exception:
            json_body = None

    request_id = None
    if isinstance(json_body, dict):
        request_id = json_body.get("request_id")
    request_id = tracker.ensure_request_id(request_id)

    handler_name = get_handler_name(name, agent_cfg)
    try:
        handler = load_agent_handler(handler_name)
    except Exception as exc:
        logger.exception({"event": "agent.handler.load_failed", "agent": name, "error": str(exc)})
        raise HTTPException(status_code=500, detail="agent_handler_missing")

    ctx = AgentContext(
        request=request,
        settings=settings,
        tracker=tracker,
        request_id=request_id,
        agent_name=name,
        agent_config=agent_cfg,
        json_body=json_body if isinstance(json_body, dict) else None,
        raw_body=raw_body or b"",
        logger=logger,
    )

    return await handler.run(ctx)
