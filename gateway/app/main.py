import asyncio
import json, httpx, logging, yaml

from .config import settings
from .logging_utils import setup_logging
from .middleware import TraceLogMiddleware, ApiKeyMiddleware
from .schemas import StdResp
from .schemas import RouteEntry
# from .routing import RouteTable
from .route_table import RouteTable, YamlRouteTable
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pathlib import Path
from prometheus_fastapi_instrumentator import Instrumentator

# --- Rate limit (slowapi) ---
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)

# ---------------- Logging ----------------
setup_logging(
    service_name="gateway",
    log_dir=settings.LOG_DIR,
    level=settings.LOG_LEVEL,
    retention_days=settings.LOG_RETENTION_DAYS,
)
logger = logging.getLogger("gateway")

# ---------------- Startup logging ----------------
def _redact(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]

logger.info(
    {
        "event": "gateway.startup_config",
        "api_prefix": settings.API_PREFIX,
        "enable_metrics": settings.ENABLE_METRICS,
        "enable_rate_limit": settings.ENABLE_RATE_LIMIT,
        "route_source": settings.ROUTE_SOURCE,
        "redis_host": settings.REDIS_HOST,
        "redis_port": settings.REDIS_PORT,
        "redis_db": settings.REDIS_DB,
        "redis_password": _redact(settings.REDIS_PASSWORD),
        "redis_key_prefix": settings.REDIS_KEY_PREFIX,
        "request_timeout_sec": settings.REQUEST_TIMEOUT_SEC,
        "log_dir": settings.LOG_DIR,
        "log_level": settings.LOG_LEVEL,
    }
)

def _build_routes():
    if settings.ROUTE_SOURCE.lower() == "yaml":
        return YamlRouteTable(settings.ROUTE_FILE)
    return RouteTable(settings=settings)

# ---------------- App ----------------
app = FastAPI(title="AI Component Gateway", version="0.1.0")

app.add_middleware(TraceLogMiddleware)
app.add_middleware(ApiKeyMiddleware)
app.state.limiter = limiter
app.state.routes = None
app.state.routes_lock = asyncio.Lock()

# 指标
if settings.ENABLE_METRICS:
    Instrumentator().instrument(app).expose(app)

async def _init_routes_once(timeout_sec: float = 2.0) -> bool:
    if app.state.routes:
        return True
    async with app.state.routes_lock:
        if app.state.routes:
            return True
        try:
            app.state.routes = await asyncio.wait_for(
                asyncio.to_thread(_build_routes),
                timeout=timeout_sec,
            )
            logger.info({"event": "routes.init_ok"})
            return True
        except asyncio.TimeoutError:
            logger.warning({"event": "routes.init_timeout", "timeout_sec": timeout_sec})
            app.state.routes = None
            return False
        except Exception as exc:
            logger.exception({"event": "routes.init_failed", "error": str(exc)})
            app.state.routes = None
            return False

@app.on_event("startup")
async def _bootstrap_routes():
    asyncio.create_task(_init_routes_once())

# ---------------- Limit ----------------
@app.exception_handler(RateLimitExceeded)
def rate_limit_exceeded_handler(request, exc):
    return PlainTextResponse("Too many requests", status_code=429)

@app.get("/health")
def health():
    return PlainTextResponse("ok")

@app.get("/routes/reload")
async def reload_routes():
    ok = await _init_routes_once()
    if not ok or not app.state.routes:
        raise HTTPException(status_code=503, detail="routes_not_ready")
    app.state.routes.reload()
    logger.info({"event": "routes.reload"})
    return StdResp(code=0, message="routes reloaded").model_dump()

@app.post("/register")
def register(ep: RouteEntry):
    if not app.state.routes:
        raise HTTPException(status_code=503, detail="routes_not_ready")
    key = f"{ep.category}.{ep.action}"
    app.state.routes.add(key, ep.url)
    logger.info({"event": "routes.register", "category": ep.category, "action": ep.action, "url": ep.url})
    return {"code": 0, "msg": "ok"}

@limiter.limit("60/minute")
@app.api_route(f"{settings.API_PREFIX}" + "/{category}/{action}", methods=["GET","POST"])
async def proxy(category: str, action: str, request: Request):
    if not app.state.routes:
        raise HTTPException(status_code=503, detail="routes_not_ready")
    target = app.state.routes.resolve(category, action)
    if not target:
        logger.warning(
            {
                "event": "routes.miss",
                "category": category,
                "action": action,
                "trace_id": getattr(request.state, "trace_id", None),
                "request_id": getattr(request.state, "request_id", None),
            }
        )
        raise HTTPException(status_code=404, detail="component_not_found")

    key = request.headers.get("X-Api-Key", "")
    if settings.GW_API_KEY and key != settings.GW_API_KEY:
        raise HTTPException(status_code=401, detail="invalid_api_key")
    
    # 组装转发请求
    method = request.method
    headers = dict(request.headers)
    # 必须移除的 hop-by-hop 头，避免长度/连接语义错乱
    HOP_BY_HOP = [
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "expect",
        "accept-encoding",
    ]
    for h in HOP_BY_HOP:
        headers.pop(h, None)
        
    # 透传 Trace-ID/Request-ID
    headers.setdefault("X-Trace-Id", getattr(request.state, "trace_id", ""))
    headers.setdefault("X-Request-Id", getattr(request.state, "request_id", ""))

    # GET/POST 支持：GET 透传 query，POST 透传 json
    params = dict(request.query_params)
    body = None
    
    if method == "POST":
        raw_body = await request.body()
        if raw_body:
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except Exception:
                # 如果不是json，直接转发原始字节
                body = raw_body

    timeout = httpx.Timeout(settings.REQUEST_TIMEOUT_SEC)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            resp = await client.request(
                method, 
                target, 
                params=params, 
                headers=headers, 
                json=body if isinstance(body, dict) else None,
                content=body if isinstance(body, (bytes, str)) else None,
                )
        except httpx.TimeoutException:
            return JSONResponse(StdResp(code=504, message="upstream_timeout").model_dump(), status_code=504)
        except httpx.RequestError as e:
            logger.exception(e)
            return JSONResponse(StdResp(code=502, message="bad_gateway").model_dump(), status_code=502)

    # 统一响应
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    if not isinstance(data, (dict, list)):
        data = {"value": data}

    return JSONResponse(
        StdResp(
            code=0 if resp.status_code < 400 else resp.status_code,
            message="ok" if resp.status_code < 400 else "upstream_error",
            data=data
        ).model_dump(),
        status_code=200 if resp.status_code < 400 else 502
    )
