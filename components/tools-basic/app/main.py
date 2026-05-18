import asyncio
import logging
import os
import uuid

import httpx

from .schemas import AddReq, StdResp
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from .logging_utils import (
    get_runtime_log_level,
    outbound_extra,
    reset_log_context,
    set_log_context,
    set_runtime_log_level,
    setup_logging,
    update_log_context,
)

LOG_DIR = os.getenv("LOG_DIR", "/app/data/logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "10"))
SYSTEM_CODE = os.getenv("SYSTEM_CODE", "ai-component-hub")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(1024 * 1024)))

setup_logging(
    "tools-basic",
    LOG_DIR,
    LOG_LEVEL,
    LOG_RETENTION_DAYS,
    system_code=SYSTEM_CODE,
    max_bytes=LOG_MAX_BYTES,
)
logger = logging.getLogger("tools-basic")

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8000")
GW_API_KEY = os.getenv("GW_API_KEY")

REGISTER_RETRY_SECONDS = 2
REGISTER_MAX_ATTEMPTS = 15


def _timestamp_now() -> tuple[str, int]:
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S") + f":{now.microsecond // 1000:03d}", int(now.timestamp() * 1000)


class LogLevelPayload(BaseModel):
    level: str | None = None
    logLevel: str | None = None


class TraceLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        receive_time, receive_time_ms = _timestamp_now()
        request.state.trace_id = trace_id
        request.state.request_id = request_id
        token = set_log_context(traceId=trace_id, seqNo=request_id, serverReceiveTime=receive_time)
        try:
            response = await call_next(request)
            return_time, return_time_ms = _timestamp_now()
            update_log_context(serverReturnTime=return_time, usedTime=str(return_time_ms - receive_time_ms))
            log_completed = logger.debug if request.url.path == "/health" else logger.info
            log_completed({"event": "request.completed", "path": request.url.path, "method": request.method, "status": response.status_code})
            response.headers["X-Trace-Id"] = trace_id
            response.headers["X-Request-Id"] = request_id
            return response
        except Exception as exc:
            return_time, return_time_ms = _timestamp_now()
            update_log_context(serverReturnTime=return_time, usedTime=str(return_time_ms - receive_time_ms))
            logger.exception({"event": "request.failed", "path": request.url.path, "error": str(exc)})
            raise
        finally:
            reset_log_context(token)

async def register_to_gateway():
    endpoints = [
        {"category": "tools", "action": "echo", "url": "http://tools-basic:7001/echo"},
        {"category": "tools", "action": "add", "url": "http://tools-basic:7001/add"},
    ]

    headers = {"X-Api-Key": GW_API_KEY} if GW_API_KEY else {}

    async with httpx.AsyncClient() as client:
        for ep in endpoints:
            ok = False
            for attempt in range(1, REGISTER_MAX_ATTEMPTS + 1):
                try:
                    resp = await client.post(f"{GATEWAY_URL}/register", json=ep, headers=headers, timeout=5)
                    if resp.status_code == 200:
                        ok = True
                        logger.info(
                            {"event": "gateway.registered", "action": ep["action"], "status": resp.status_code},
                            extra=outbound_extra(),
                        )
                        break
                    logger.warning(
                        {
                            "event": "gateway.register.failed",
                            "action": ep["action"],
                            "status": resp.status_code,
                            "attempt": attempt,
                        },
                        extra=outbound_extra(),
                    )
                except Exception as e:
                    logger.warning(
                        {
                            "event": "gateway.register.error",
                            "action": ep["action"],
                            "attempt": attempt,
                            "error": str(e),
                        },
                        extra=outbound_extra(),
                    )
                await asyncio.sleep(REGISTER_RETRY_SECONDS)
            if not ok:
                logger.error({"event": "gateway.register.giveup", "action": ep["action"]}, extra=outbound_extra())


@asynccontextmanager
async def lifespan(app: FastAPI):
    await register_to_gateway()
    yield

app = FastAPI(title="tools-basic", lifespan=lifespan)
app.add_middleware(TraceLogMiddleware)

@app.get("/health")
def health():
    return "ok"


@app.get("/log-level")
def get_log_level():
    return {
        "logLevel": get_runtime_log_level(),
        "effectiveLevels": {
            "root": logging.getLevelName(logging.getLogger().getEffectiveLevel()),
            "tools-basic": logging.getLevelName(logging.getLogger("tools-basic").getEffectiveLevel()),
            "uvicorn": logging.getLevelName(logging.getLogger("uvicorn").getEffectiveLevel()),
        },
    }


@app.post("/log-level")
def update_log_level(payload: LogLevelPayload):
    requested_level = payload.level or payload.logLevel
    if not requested_level:
        raise HTTPException(status_code=400, detail="missing level or logLevel")
    before = get_runtime_log_level()
    level = set_runtime_log_level(requested_level)
    logger.warning({"event": "log.level.changed", "before": before, "after": level})
    return {
        "logLevel": level,
        "effectiveLevels": {
            "root": logging.getLevelName(logging.getLogger().getEffectiveLevel()),
            "tools-basic": logging.getLevelName(logging.getLogger("tools-basic").getEffectiveLevel()),
            "uvicorn": logging.getLevelName(logging.getLogger("uvicorn").getEffectiveLevel()),
        },
    }

@app.get("/echo")
def echo(q: str = "hello"):
    logger.info({"event": "tools.echo", "q": q})
    return StdResp(data={"echo": q})

@app.post("/add")
def add(req: AddReq):
    logger.info({"event": "tools.add", "a": req.a, "b": req.b})
    return StdResp(data={"sum": req.a + req.b})
