from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..logging_utils import outbound_extra


async def send_callback(
    *,
    callback_url: str,
    payload: dict[str, Any] | None = None,
    body: str | None = None,
    content_type: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float,
    max_retries: int,
    base_delay: float,
    logger,
    request_id: str,
    trace_id: str | None,
) -> dict[str, Any]:
    if not callback_url:
        logger.info(
            {"event": "callback.skip", "request_id": request_id, "trace_id": trace_id},
            extra=outbound_extra(),
        )
        return {"status": "SKIPPED", "error": None, "attempts": 0}

    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                send_headers = dict(headers or {})
                if content_type:
                    send_headers.setdefault("Content-Type", content_type)
                if body is not None:
                    resp = await client.post(callback_url, content=body, headers=send_headers)
                else:
                    resp = await client.post(callback_url, json=payload, headers=send_headers)
                resp.raise_for_status()
            logger.info(
                {
                    "event": "callback.ok",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "attempt": attempt,
                    "content_type": content_type or "application/json",
                },
                extra=outbound_extra(),
            )
            return {"status": "OK", "error": None, "attempts": attempt}
        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                {
                    "event": "callback.failed",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "attempt": attempt,
                    "error": last_error,
                },
                extra=outbound_extra(faultCode="CALLBACK_RETRY"),
            )
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

    logger.error(
        {
            "event": "callback.giveup",
            "request_id": request_id,
            "trace_id": trace_id,
            "error": last_error,
        },
        extra=outbound_extra(faultCode="CALLBACK_FAILED"),
    )
    return {"status": "FAILED", "error": last_error, "attempts": max_retries}
