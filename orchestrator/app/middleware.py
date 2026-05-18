from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings
from .logging_utils import reset_log_context, resolve_chanl_no, set_log_context, update_log_context


logger = logging.getLogger("orchestrator")


def _timestamp_now() -> tuple[str, int]:
    now = datetime.now()
    formatted = now.strftime("%Y-%m-%d %H:%M:%S") + f":{now.microsecond // 1000:03d}"
    epoch_ms = int(now.timestamp() * 1000)
    return formatted, epoch_ms


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        receive_time, receive_time_ms = _timestamp_now()
        chanl_no = resolve_chanl_no(request=request, settings=settings)

        request.state.trace_id = trace_id
        request.state.request_id = request_id

        token = set_log_context(
            seqNo=request_id,
            sysTraceId=trace_id,
            sysSpaInd=request_id,
            sysParentSpaInd="",
            svrName="orchestrator",
            svCode=request.url.path,
            chanlNo=chanl_no,
            faultCode="",
            serverReceiveTime=receive_time,
        )
        try:
            response = await call_next(request)
            return_time, return_time_ms = _timestamp_now()
            fault_code = "" if response.status_code < 400 else str(response.status_code)
            update_log_context(
                serverReturnTime=return_time,
                usedTime=str(return_time_ms - receive_time_ms),
                faultCode=fault_code,
                svCode=request.url.path,
            )
            log_completed = logger.debug if request.url.path == "/health" else logger.info
            log_completed(
                {
                    "event": "request.completed",
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                }
            )
            response.headers["X-Trace-Id"] = trace_id
            response.headers["X-Request-Id"] = request_id
            return response
        except Exception as exc:
            return_time, return_time_ms = _timestamp_now()
            update_log_context(
                serverReturnTime=return_time,
                usedTime=str(return_time_ms - receive_time_ms),
                faultCode="500",
                svCode=request.url.path,
            )
            logger.exception(
                {
                    "event": "request.failed",
                    "method": request.method,
                    "path": request.url.path,
                    "error": str(exc),
                }
            )
            raise
        finally:
            reset_log_context(token)
