from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..services.file_stage import (
    download_to_staging,
    split_url_for_esb,
    upload_json_via_esb,
)
from ..services.agent_client import AgentClient
from ..services.agent_config_store import get_agent_config
from ..services.job_tracker import JobTracker
from ..schemas.document_ocr_schemas import DocOCRReq, DocOCRResp


router = APIRouter()
logger = logging.getLogger("agents")


@router.post("/doc-ocr/run", response_model=DocOCRResp)
async def run_doc_ocr(req: DocOCRReq, request: Request):
    """
    最小可上线版本：
    - 生成/使用 request_id（幂等）
    - Redis 记录 job 状态与结果（无状态）
    - 下载文件到外部 volume staging（流式）
    - 调用智能体平台（先 stub）
    """
    r = request.app.state.redis  # type: ignore[attr-defined]
    tracker = JobTracker(r)

    request_id = tracker.ensure_request_id(req.request_id)
    trace_id = request.headers.get("X-Trace-Id")
    logger.info({"event": "doc_ocr.received", "request_id": request_id, "trace_id": trace_id})

    # 1) 幂等：如果已经有结果/状态，直接返回
    _, existing = tracker.get_job(request_id)
    if existing:
        return DocOCRResp(
            request_id=request_id,
            status=existing.get("status", "UNKNOWN"),
            result=existing.get("result"),
            error=existing.get("error"),
        )

    # 2) 分布式锁：避免同一 request_id 并发重复执行
    token, _ = tracker.acquire_lock(request_id, ttl=settings.IDEMPOTENCY_TTL_SEC)
    if not token:
        # 有另一个实例在跑；返回 RUNNING（调用方可重试）
        return DocOCRResp(request_id=request_id, status="RUNNING")

    try:
        # 3) 写入 RUNNING 状态（带 TTL）
        tracker.set_status(request_id, status="RUNNING", result=None, error=None, ttl=settings.JOB_TTL_SEC)
        logger.info({"event": "doc_ocr.running", "request_id": request_id, "trace_id": trace_id})

        # 4) 下载文件到 staging（外部卷路径）
        filename = req.file.filename or "input.bin"
        staged = await download_to_staging(
            request_id=request_id,
            url=req.file.url,
            staging_dir=settings.STAGING_DIR,
            filename=filename,
            timeout=120.0,
        )

        # 5) 调用智能体平台（一期 stub）
        agent_config = get_agent_config(r, "doc-ocr")
        if not agent_config:
            tracker.set_status(
                request_id,
                status="FAILED",
                result=None,
                error="agent_config_missing",
                ttl=settings.JOB_TTL_SEC,
            )
            logger.error({"event": "doc_ocr.config_missing", "request_id": request_id, "trace_id": trace_id})
            raise HTTPException(status_code=500, detail="agent_config_missing")

        client = AgentClient(
            base_url=agent_config.get("url", ""),
            conversation_url=agent_config.get("conversation_url", ""),
            upload_url=agent_config.get("upload_url", ""),
            run_url=agent_config.get("run_url", ""),
            authorization=agent_config.get("authorization", ""),
            app_id=agent_config.get("app_id", ""),
            department_id=agent_config.get("department_id", ""),
        )
        agent_res = await client.run_doc_ocr(local_file_path=staged.local_path, options=req.options)

        if not agent_res.ok:
            tracker.set_status(
                request_id,
                status="FAILED",
                result=None,
                error=agent_res.error,
                ttl=settings.JOB_TTL_SEC,
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

        # 6) 写入 UPLOADING 状态（带 TTL）
        result = {
            "staged": {
                "url": staged.url,
                "local_path": staged.local_path,
                "size_bytes": staged.size_bytes,
                "sha256": staged.sha256,
            },
            "agent": agent_res.data,
        }
        tracker.set_status(request_id, status="UPLOADING", result=result, error=None, ttl=settings.JOB_TTL_SEC)
        logger.info({"event": "doc_ocr.uploading", "request_id": request_id, "trace_id": trace_id})

        # 7) 上传智能体结果到文件服务器（通过 ESB）
        server_path, _ = split_url_for_esb(req.file.url)
        upload_filename = f"{request_id}-result.json"
        upload_path = Path(settings.STAGING_DIR) / request_id / upload_filename
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        upload_path.write_text(json.dumps(agent_res.data, ensure_ascii=False), encoding="utf-8")

        try:
            await upload_json_via_esb(
                server_path=server_path,
                server_file=upload_filename,
                payload=agent_res.data,
                local_file_path=str(upload_path),
            )
        except Exception as e:
            tracker.set_status(
                request_id,
                status="FAILED",
                result=None,
                error=f"upload_failed: {e}",
                ttl=settings.JOB_TTL_SEC,
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

        result["esb_upload"] = {"server_path": server_path, "server_file": upload_filename}
        tracker.set_status(request_id, status="SUCCEEDED", result=result, error=None, ttl=settings.JOB_TTL_SEC)
        logger.info({"event": "doc_ocr.succeeded", "request_id": request_id, "trace_id": trace_id})
        
        
        return DocOCRResp(request_id=request_id, status="SUCCEEDED", result=result)

    finally:
        # 7) 解锁
        tracker.release_lock(request_id, token)
