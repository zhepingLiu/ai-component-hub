from __future__ import annotations

import asyncio
from typing import Any

import httpx


async def send_callback(
    *,
    callback_url: str,
    payload: dict[str, Any],
    timeout: float,
    max_retries: int,
    base_delay: float,
    logger,
    request_id: str,
    trace_id: str | None,
) -> None:
    if not callback_url:
        logger.info({"event": "callback.skip", "request_id": request_id, "trace_id": trace_id})
        return

    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(callback_url, json=payload)
                resp.raise_for_status()
            logger.info(
                {
                    "event": "callback.ok",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "attempt": attempt,
                }
            )
            return
        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                {
                    "event": "callback.failed",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "attempt": attempt,
                    "error": last_error,
                }
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
        }
    )
