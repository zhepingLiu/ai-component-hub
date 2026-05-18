from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...core.context import AgentExecutionContext
from ...services.file_stage import split_url_for_esb, upload_json_via_esb
from .schema import FileRef


async def upload_doc_ocr_result(
    *,
    ctx: AgentExecutionContext,
    file_refs: list[FileRef],
    agent_data: dict[str, Any],
) -> dict[str, str]:
    server_paths = []
    for file_ref in file_refs:
        if file_ref.url:
            server_path, _ = split_url_for_esb(file_ref.url)
        else:
            server_path = ctx.settings.ESB_BASE_URL.rstrip("/")
        server_paths.append(server_path)

    primary_server_path = server_paths[0]
    if len(set(server_paths)) > 1:
        ctx.logger.warning(
            {
                "event": "doc_ocr.multiple_server_paths",
                "request_id": ctx.request_id,
                "trace_id": ctx.trace_id,
                "server_paths": server_paths,
            }
        )

    upload_subdir = "doc_ocr"
    upload_filename = f"{upload_subdir}/{ctx.request_id}-result.json"
    upload_path = Path(ctx.settings.STAGING_DIR) / ctx.request_id / upload_filename
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_text(json.dumps(agent_data, ensure_ascii=False), encoding="utf-8")

    await upload_json_via_esb(
        server_path=primary_server_path,
        server_file=upload_filename,
        payload=agent_data,
        local_file_path=str(upload_path),
        timeout=ctx.settings.ESB_UPLOAD_TIMEOUT_SEC,
    )
    return {"server_path": primary_server_path, "server_file": upload_filename}
