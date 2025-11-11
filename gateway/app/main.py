import json, httpx, logging, yaml

from .config import settings
from .db import init_db
from .middleware import TraceLogMiddleware, ApiKeyMiddleware
from .schemas import StdResp
from .routing import RouteTable
from contextlib import asynccontextmanager
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
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("gateway")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("[gateway] SQLite DB initialized.")
    yield
    print("[gateway] shutting down...") 


# ---------------- App ----------------
app = FastAPI(title="AI Component Gateway", version="0.1.0", lifespan=lifespan)

app.add_middleware(TraceLogMiddleware)
app.add_middleware(ApiKeyMiddleware)
app.state.limiter = limiter

# 指标
if settings.ENABLE_METRICS:
    Instrumentator().instrument(app).expose(app)

# ---------------- Load routes ----------------
routes = RouteTable(settings.ROUTE_FILE)

# ---------------- Limit ----------------
@app.exception_handler(RateLimitExceeded)
def rate_limit_exceeded_handler(request, exc):
    return PlainTextResponse("Too many requests", status_code=429)

@app.get("/health")
def health():
    return PlainTextResponse("ok")

@app.get("/routes/reload")
def reload_routes():
    routes.reload()
    return StdResp(code=0, message="routes reloaded").model_dump()

@app.post("/register")
async def register_component(request: Request):
    """
    组件启动时自动调用:
    POST /register
    body: {"category": "tools", "action": "echo", "url": "http://tools-basic:7001/echo"}
    """
    payload = await request.json()
    category = payload.get("category")
    action = payload.get("action")
    url = payload.get("url")

    if not all([category, action, url]):
        raise HTTPException(status_code=400, detail="missing_fields")

    routes[f"{category}.{action}"] = url
    
    print(routes)

    # 更新文件（可选：同步写回 routes.yaml）
    try:
        with open(settings.ROUTE_FILE, "w") as f:
            yaml.safe_dump(routes._routes, f)
    except Exception as e:
        logger.warning(f"Failed to update route file: {e}")

    return StdResp(code=0, message="component_registered", data={"route": f"{category}.{action}"}).model_dump()

@limiter.limit("60/minute")
@app.api_route(f"{settings.API_PREFIX}" + "/{category}/{action}", methods=["GET","POST"])
async def proxy(category: str, action: str, request: Request):
    target = routes.resolve(category, action)
    if not target:
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
        
    # 透传 Trace-ID
    headers.setdefault("X-Trace-Id", request.headers.get("X-Trace-Id", ""))

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

    return JSONResponse(
        StdResp(
            code=0 if resp.status_code < 400 else resp.status_code,
            message="ok" if resp.status_code < 400 else "upstream_error",
            data=data
        ).model_dump(),
        status_code=200 if resp.status_code < 400 else 502
    )
