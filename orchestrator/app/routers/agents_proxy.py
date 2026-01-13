from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import settings
from ..schemas.route_schemas import StdResp


router = APIRouter()
logger = logging.getLogger("orchestrator")


@router.post("/doc-ocr/run")
async def proxy_doc_ocr(request: Request):
    target = settings.AGENTS_BASE_URL.rstrip("/") + "/agents/doc-ocr/run"

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

    raw_body = await request.body()
    body = None
    if raw_body:
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception:
            body = raw_body

    timeout = httpx.Timeout(settings.REQUEST_TIMEOUT_SEC)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            resp = await client.request(
                "POST",
                target,
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
