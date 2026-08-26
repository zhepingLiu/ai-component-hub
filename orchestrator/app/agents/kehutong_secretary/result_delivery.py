from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...batch.definition import BatchContext
from ...batch.store import RedisBatchStore


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return normalized or "request"


async def publish_kehutong_result(
    *,
    ctx: BatchContext,
    request_id: str,
    agent_name: str,
    agent_config: dict[str, Any],
) -> dict[str, Any]:
    server_path = str(agent_config.get("result_server_path", "")).strip()
    public_base_url = str(agent_config.get("result_public_base_url", "")).strip()
    if not server_path:
        raise RuntimeError("result_server_path is not configured")
    if not public_base_url:
        raise RuntimeError("result_public_base_url is not configured")

    from ...redis_client import create_redis_client

    store = RedisBatchStore(
        create_redis_client(),
        key_prefix=ctx.settings.BATCH_REDIS_KEY_PREFIX,
        retention_seconds=ctx.settings.BATCH_RETENTION_SEC,
    )
    tasks = []
    cursor = 0
    while True:
        page, next_cursor = await store.list_tasks(ctx.batch.batch_id, offset=cursor, limit=200)
        tasks.extend(page)
        if next_cursor is None:
            break
        cursor = next_cursor

    customers = []
    for task in tasks:
        item = {
            "customer_id": task.business_key,
            "status": str(task.status),
            "attempts": task.attempt,
        }
        if task.output is not None:
            item["result"] = task.output.get("result", task.output)
        if task.error:
            item["error"] = task.error
        customers.append(item)

    summary = {
        "total": ctx.batch.total,
        "succeeded": ctx.batch.succeeded,
        "failed": ctx.batch.failed,
        "cancelled": ctx.batch.cancelled,
    }
    document = {
        "request_id": request_id,
        "batch_id": ctx.batch.batch_id,
        "agent": agent_name,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "customers": customers,
    }
    subdir = _safe_name(str(agent_config.get("result_subdir", "kehutong_secretary")))
    request_name = _safe_name(request_id)
    server_file = f"{subdir}/{request_name}-result.json"
    local_path = Path(ctx.settings.STAGING_DIR) / request_name / server_file
    local_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    local_path.write_text(content, encoding="utf-8")

    from ...services.file_stage import upload_json_via_esb

    await upload_json_via_esb(
        server_path=server_path,
        server_file=server_file,
        payload=document,
        local_file_path=str(local_path),
        timeout=ctx.settings.ESB_UPLOAD_TIMEOUT_SEC,
    )
    encoded = content.encode("utf-8")
    return {
        "summary": summary,
        "result_url": f"{public_base_url.rstrip('/')}/{server_file}",
        "server_file": server_file,
        "size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
