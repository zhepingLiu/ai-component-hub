import logging
import uuid
from datetime import datetime
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from .config import settings
from .logging_utils import reset_log_context, set_log_context, update_log_context

logger = logging.getLogger("gateway")


def _timestamp_now() -> tuple[str, int]:
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S") + f":{now.microsecond // 1000:03d}", int(now.timestamp() * 1000)

class TraceLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        receive_time, receive_time_ms = _timestamp_now()
        request.state.trace_id = trace_id
        request.state.request_id = request_id
        token = set_log_context(
            traceId=trace_id,
            seqNo=request_id,
            serverReceiveTime=receive_time,
        )
        try:
            response = await call_next(request)
            return_time, return_time_ms = _timestamp_now()
            update_log_context(
                serverReturnTime=return_time,
                usedTime=str(return_time_ms - receive_time_ms),
            )
            log_completed = logger.debug if request.url.path == "/health" else logger.info
            log_completed({
                "event": "request.completed",
                "trace_id": trace_id,
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "status": response.status_code,
            })
            response.headers["X-Trace-Id"] = trace_id
            response.headers["X-Request-Id"] = request_id
            return response
        except Exception as e:
            return_time, return_time_ms = _timestamp_now()
            update_log_context(
                serverReturnTime=return_time,
                usedTime=str(return_time_ms - receive_time_ms),
            )
            logger.exception({"event": "request.failed", "trace_id": trace_id, "err": str(e)})
            raise
        finally:
            reset_log_context(token)

class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if settings.GW_API_KEY:
            key = request.headers.get("X-Api-Key")
            if key != settings.GW_API_KEY:
                raise HTTPException(status_code=401, detail="unauthorized")
        return await call_next(request)
