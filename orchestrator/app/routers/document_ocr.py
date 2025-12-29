from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..services.file_stage import download_to_staging
from ..services.agent_client import AgentClient
from ..schemas.document_ocr import DocOCRReq, DocOCRResp


router = APIRouter()

# --------- Redis key helpers ---------

def _k(*parts: str) -> str:
    # e.g. aihub:orchestrator:job:<request_id>
    return f"{settings.REDIS_KEY_PREFIX}:" + ":".join(parts)


JOB_KEY = "job"          # job:<rid>  -> hash/json
LOCK_KEY = "lock"        # lock:<rid> -> string token


@router.post("/doc-ocr/run", response_model=DocOCRResp)
async def run_doc_ocr(req: DocOCRReq, request: Request):
    """
    最小可上线版本：
    - 生成/使用 request_id（幂等）
    - Redis 记录 job 状态与结果（无状态）
    - 下载文件到外部 volume staging（流式）
    - 调用智能体平台（先 stub）
    """
    r = request.app.state.redis

    request_id = req.request_id or str(uuid.uuid4())
    job_key = _k(JOB_KEY, request_id)
    lock_key = _k(LOCK_KEY, request_id)

    # 1) 幂等：如果已经有结果/状态，直接返回
    existing = r.get(job_key)
    if existing:
        payload = json.loads(existing)
        return DocOCRResp(
            request_id=request_id,
            status=payload.get("status", "UNKNOWN"),
            result=payload.get("result"),
            error=payload.get("error"),
        )

    # 2) 分布式锁：避免同一 request_id 并发重复执行
    token = str(uuid.uuid4())
    got_lock = r.set(lock_key, token, nx=True, ex=settings.IDEMPOTENCY_TTL_SEC)
    if not got_lock:
        # 有另一个实例在跑；返回 RUNNING（调用方可重试）
        return DocOCRResp(request_id=request_id, status="RUNNING")

    try:
        # 3) 写入 RUNNING 状态（带 TTL）
        r.set(
            job_key,
            json.dumps({"status": "RUNNING", "result": None, "error": None}),
            ex=settings.JOB_TTL_SEC,
        )

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
        client = AgentClient(base_url="")  # 后续接真实平台时从 env/config 注入 base_url
        agent_res = await client.run_doc_ocr(local_file_path=staged.local_path, options=req.options)

        if not agent_res.ok:
            r.set(
                job_key,
                json.dumps({"status": "FAILED", "result": None, "error": agent_res.error}),
                ex=settings.JOB_TTL_SEC,
            )
            raise HTTPException(status_code=502, detail=agent_res.error or "agent upstream error")

        # 6) 写入 SUCCEEDED 结果（带 TTL）
        result = {
            "staged": {
                "url": staged.url,
                "local_path": staged.local_path,
                "size_bytes": staged.size_bytes,
                "sha256": staged.sha256,
            },
            "agent": agent_res.data,
        }
        r.set(job_key, json.dumps({"status": "SUCCEEDED", "result": result, "error": None}), ex=settings.JOB_TTL_SEC)

        return DocOCRResp(request_id=request_id, status="SUCCEEDED", result=result)

    finally:
        # 7) 解锁（最佳努力）
        try:
            cur = r.get(lock_key)
            if cur == token:
                r.delete(lock_key)
        except Exception:
            pass
