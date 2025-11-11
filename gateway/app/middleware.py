import time, uuid, logging
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from .config import settings

logger = logging.getLogger("gateway")

class TraceLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        start = time.time()
        try:
            response = await call_next(request)
            duration = round((time.time() - start)*1000, 2)
            logger.info({
                "trace_id": trace_id,
                "path": request.url.path,
                "method": request.method,
                "status": response.status_code,
                "ms": duration
            })
            response.headers["X-Trace-Id"] = trace_id
            return response
        except Exception as e:
            duration = round((time.time() - start)*1000, 2)
            logger.exception({"trace_id": trace_id, "err": str(e), "ms": duration})
            raise

class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if settings.GW_API_KEY:
            key = request.headers.get("X-Api-Key")
            if key != settings.GW_API_KEY:
                raise HTTPException(status_code=401, detail="unauthorized")
        return await call_next(request)
