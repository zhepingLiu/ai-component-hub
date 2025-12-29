from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/health")
def health(request: Request):
    r = request.app.state.redis
    try:
        r.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    return {"status": "ok", "service": "orchestrator", "redis": redis_ok}
