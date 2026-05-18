from __future__ import annotations

import asyncio
import logging
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request

from .logging_utils import get_runtime_log_level, set_runtime_log_level

router = APIRouter()
logger = logging.getLogger("orchestrator")


class LogLevelPayload(BaseModel):
    level: str | None = None
    logLevel: str | None = None

@router.get("/health")
async def health(request: Request):
    r = request.app.state.redis
    if not r:
        redis_ok = False
    else:
        try:
            await asyncio.to_thread(r.ping)
            redis_ok = True
        except Exception:
            redis_ok = False

    queues = getattr(request.app.state, "agent_queues", {}) or {}
    queue_stats = {name: await queue.snapshot() for name, queue in queues.items()}
    return {
        "status": "ok",
        "service": "orchestrator",
        "redis": redis_ok,
        "agents": queue_stats,
        "doc_ocr": queue_stats.get("doc-ocr"),
    }


@router.get("/log-level")
def get_log_level():
    return {
        "logLevel": get_runtime_log_level(),
        "effectiveLevels": {
            "root": logging.getLevelName(logging.getLogger().getEffectiveLevel()),
            "orchestrator": logging.getLevelName(logging.getLogger("orchestrator").getEffectiveLevel()),
            "uvicorn": logging.getLevelName(logging.getLogger("uvicorn").getEffectiveLevel()),
            "uvicorn.error": logging.getLevelName(logging.getLogger("uvicorn.error").getEffectiveLevel()),
            "uvicorn.access": logging.getLevelName(logging.getLogger("uvicorn.access").getEffectiveLevel()),
        },
    }


@router.post("/log-level")
def update_log_level(payload: LogLevelPayload):
    try:
        requested_level = payload.level or payload.logLevel
        if not requested_level:
            raise HTTPException(status_code=400, detail="missing level or logLevel")
        before_level = get_runtime_log_level()
        level = set_runtime_log_level(requested_level)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.warning({"event": "log.level.changed", "before": before_level, "after": level})
    return {
        "logLevel": level,
        "effectiveLevels": {
            "root": logging.getLevelName(logging.getLogger().getEffectiveLevel()),
            "orchestrator": logging.getLevelName(logging.getLogger("orchestrator").getEffectiveLevel()),
            "uvicorn": logging.getLevelName(logging.getLogger("uvicorn").getEffectiveLevel()),
            "uvicorn.error": logging.getLevelName(logging.getLogger("uvicorn.error").getEffectiveLevel()),
            "uvicorn.access": logging.getLevelName(logging.getLogger("uvicorn.access").getEffectiveLevel()),
        },
    }
