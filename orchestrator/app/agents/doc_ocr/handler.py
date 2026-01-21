from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import ValidationError

from ...services.agent_runtime import AgentContext
from ...services.file_stage import (
    download_to_staging,
    split_url_for_esb,
    upload_json_via_esb,
)
from .client import DocOCRClient
from .schema import DocOCRReq, DocOCRResp


async def run(ctx: AgentContext):
    if ctx.json_body is None:
        raise HTTPException(status_code=400, detail="invalid_json")
    try:
        req = DocOCRReq.model_validate(ctx.json_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    tracker = ctx.tracker
    request_id = ctx.request_id
    trace_id = ctx.request.headers.get("X-Trace-Id")
    logger = ctx.logger
    cfg = ctx.settings

    agent_cfg = ctx.agent_config or {}

    def _cfg(*keys: str) -> str:
        for k in keys:
            v = agent_cfg.get(k)
            if isinstance(v, str) and v:
                return v
        return ""

    client = DocOCRClient(
        base_url=_cfg("base_url", "host"),
        conversation_url=_cfg("conversation_url"),
        upload_url=_cfg("upload_url"),
        run_url=_cfg("run_url"),
        authorization=_cfg("authorization", "private_key", "secret"),
        app_id=_cfg("app_id", "appId"),
        department_id=_cfg("department_id", "departmentId"),
    )

    logger.info({"event": "doc_ocr.received", "request_id": request_id, "trace_id": trace_id})

    _, existing = tracker.get_job(request_id)
    if existing:
        return DocOCRResp(
            request_id=request_id,
            status=existing.get("status", "UNKNOWN"),
            result=existing.get("result"),
            error=existing.get("error"),
        )

    token, _ = tracker.acquire_lock(request_id, ttl=cfg.IDEMPOTENCY_TTL_SEC)
    if not token:
        return DocOCRResp(request_id=request_id, status="RUNNING")

    try:
        tracker.set_status(request_id, status="RUNNING", result=None, error=None, ttl=cfg.JOB_TTL_SEC)
        logger.info({"event": "doc_ocr.running", "request_id": request_id, "trace_id": trace_id})

        file_refs = list(req.files)
        if req.file:
            file_refs.insert(0, req.file)

        used_filenames: set[str] = set()
        staged_files = []
        for idx, file_ref in enumerate(file_refs):
            filename = file_ref.filename
            if not filename:
                parsed_name = Path(urlsplit(file_ref.url).path).name
                filename = parsed_name or f"input-{idx + 1}.bin"
            if filename in used_filenames:
                stem = Path(filename).stem or "input"
                suffix = Path(filename).suffix
                filename = f"{stem}-{idx + 1}{suffix}"
            used_filenames.add(filename)

            staged = await download_to_staging(
                request_id=request_id,
                url=file_ref.url,
                staging_dir=cfg.STAGING_DIR,
                filename=filename,
                timeout=cfg.STAGING_DOWNLOAD_TIMEOUT_SEC,
            )
            staged_files.append(staged)

        use_real = agent_cfg.get("use_real", False) or bool(
            agent_cfg.get("base_url")
            or agent_cfg.get("host")
            or agent_cfg.get("conversation_url")
            or agent_cfg.get("upload_url")
            or agent_cfg.get("run_url")
            or agent_cfg.get("app_id")
            or agent_cfg.get("appId")
        )
        local_paths = [staged.local_path for staged in staged_files]
        if use_real:
            if len(local_paths) == 1:
                agent_res = await client.run_doc_ocr_real(local_file_path=local_paths[0], options=req.options)
            else:
                agent_res = await client.run_doc_ocr_real_many(local_file_paths=local_paths, options=req.options)
        else:
            if len(local_paths) == 1:
                agent_res = await client.run_doc_ocr(local_file_path=local_paths[0], options=req.options)
            else:
                agent_res = await client.run_doc_ocr_many(local_file_paths=local_paths, options=req.options)

        if not agent_res.ok:
            tracker.set_status(
                request_id,
                status="FAILED",
                result=None,
                error=agent_res.error,
                ttl=cfg.JOB_TTL_SEC,
            )
            logger.error(
                {
                    "event": "doc_ocr.failed",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "error": agent_res.error,
                }
            )
            raise HTTPException(status_code=502, detail=agent_res.error or "agent upstream error")

        result = {
            "staged": [
                {
                    "url": staged.url,
                    "local_path": staged.local_path,
                    "size_bytes": staged.size_bytes,
                    "sha256": staged.sha256,
                }
                for staged in staged_files
            ],
            "agent": agent_res.data,
        }
        tracker.set_status(request_id, status="UPLOADING", result=result, error=None, ttl=cfg.JOB_TTL_SEC)
        logger.info({"event": "doc_ocr.uploading", "request_id": request_id, "trace_id": trace_id})

        server_paths = []
        for file_ref in file_refs:
            server_path, _ = split_url_for_esb(file_ref.url)
            server_paths.append(server_path)
        primary_server_path = server_paths[0]
        if len(set(server_paths)) > 1:
            logger.warning(
                {
                    "event": "doc_ocr.multiple_server_paths",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "server_paths": server_paths,
                }
            )
        upload_filename = f"{request_id}-result.json"
        upload_path = Path(cfg.STAGING_DIR) / request_id / upload_filename
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        upload_path.write_text(json.dumps(agent_res.data, ensure_ascii=False), encoding="utf-8")

        try:
            await upload_json_via_esb(
                server_path=primary_server_path,
                server_file=upload_filename,
                payload=agent_res.data,
                local_file_path=str(upload_path),
                timeout=cfg.ESB_UPLOAD_TIMEOUT_SEC,
            )
        except Exception as e:
            tracker.set_status(
                request_id,
                status="FAILED",
                result=None,
                error=f"upload_failed: {e}",
                ttl=cfg.JOB_TTL_SEC,
            )
            logger.error(
                {
                    "event": "doc_ocr.upload_failed",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "error": str(e),
                }
            )
            raise HTTPException(status_code=502, detail="upload_to_esb_failed")

        result["esb_upload"] = {"server_path": primary_server_path, "server_file": upload_filename}
        tracker.set_status(request_id, status="SUCCEEDED", result=result, error=None, ttl=cfg.JOB_TTL_SEC)
        logger.info({"event": "doc_ocr.succeeded", "request_id": request_id, "trace_id": trace_id})

        return DocOCRResp(request_id=request_id, status="SUCCEEDED", result=result)

    finally:
        tracker.release_lock(request_id, token)
