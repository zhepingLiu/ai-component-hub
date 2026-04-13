from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request

from .logging_utils import get_runtime_log_level, set_runtime_log_level

router = APIRouter()


class LogLevelPayload(BaseModel):
    level: str

@router.get("/health")
def health(request: Request):
    r = request.app.state.redis
    if not r:
        redis_ok = False
    else:
        try:
            r.ping()
            redis_ok = True
        except Exception:
            redis_ok = False

    return {"status": "ok", "service": "orchestrator", "redis": redis_ok}


@router.get("/log-level")
def get_log_level():
    return {"logLevel": get_runtime_log_level()}


@router.post("/log-level")
def update_log_level(payload: LogLevelPayload):
    try:
        level = set_runtime_log_level(payload.level)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"logLevel": level}
