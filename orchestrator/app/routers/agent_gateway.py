from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..config import settings
from ..redis_client import get_redis
from ..route_table import RouteTable
from ..schemas.route_schemas import RouteEntry, StdResp


router = APIRouter()
logger = logging.getLogger("orchestrator")


@router.post("/register")
def register(ep: RouteEntry, request: Request):
    table = RouteTable(get_redis(request.app), settings.REDIS_KEY_PREFIX)
    key = f"{ep.category}.{ep.action}"
    table.add(key, ep.url)
    logger.info({"event": "routes.register", "category": ep.category, "action": ep.action, "url": ep.url})
    return {"code": 0, "msg": "ok"}


def _resolve_agent_target(request: Request, name: str) -> tuple[str | None, dict, dict]:
    configs = getattr(request.app.state, "agent_configs", {}) or {}
    cfg = configs.get(name) if isinstance(configs, dict) else None

    if cfg:
        url = cfg.get("url")
        if not url:
            base = cfg.get("base_url") or cfg.get("host")
            path = cfg.get("path", "")
            if base:
                url = str(base).rstrip("/") + "/" + str(path).lstrip("/")
        query = cfg.get("query", {}) or {}
        headers = cfg.get("headers", {}) or {}
        return url, dict(query), dict(headers)

    table = RouteTable(get_redis(request.app), settings.REDIS_KEY_PREFIX)
    return table.resolve("agents", name), {}, {}


@router.api_route("/api/agents/{name}", methods=["GET", "POST"])
async def proxy_agent(name: str, request: Request):
    target, extra_query, extra_headers = _resolve_agent_target(request, name)
    if not target:
        logger.warning(
            {
                "event": "routes.miss",
                "category": "agents",
                "action": name,
                "trace_id": request.headers.get("X-Trace-Id"),
                "request_id": request.headers.get("X-Request-Id"),
            }
        )
        raise HTTPException(status_code=404, detail="agent_not_found")

    method = request.method
    headers = dict(request.headers)
    hop_by_hop = [
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "expect",
        "accept-encoding",
    ]
    for h in hop_by_hop:
        headers.pop(h, None)

    headers.setdefault("X-Trace-Id", request.headers.get("X-Trace-Id", ""))
    headers.setdefault("X-Request-Id", request.headers.get("X-Request-Id", ""))
    headers.update(extra_headers)

    params = dict(request.query_params)
    params.update(extra_query)
    body = None

    if method == "POST":
        raw_body = await request.body()
        if raw_body:
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except Exception:
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

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    return JSONResponse(
        StdResp(
            code=0 if resp.status_code < 400 else resp.status_code,
            message="ok" if resp.status_code < 400 else "upstream_error",
            data=data,
        ).model_dump(),
        status_code=200 if resp.status_code < 400 else 502,
    )
