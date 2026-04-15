from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime
from typing import Mapping
from xml.etree import ElementTree as ET

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...services.agent_runtime import AgentContext
from ...services.callbacks import send_callback
from ...services.file_stage import (
    download_to_staging,
    split_url_for_esb,
    upload_json_via_esb,
)
from .client import DocOCRClient
from .schema import DocOCRReq, DocOCRResp


_REQUEST_HEADER_KEYS = [
    "ChanlNo",
    "ReqSeqNo",
    "ReqTime",
    "ReqDate",
]

_REQUEST_BODY_KEYS = [
    "BusinessSerlNo",
    "ResultFlag",
    "FilePathAddr",
    "FailReason",
]


def _xml_text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _append_children(parent: ET.Element, keys: list[str], values: Mapping[str, object]) -> None:
    for key in keys:
        child = ET.SubElement(parent, key)
        child.text = _xml_text(values.get(key, ""))


def _build_doc_ocr_callback_xml(
    *,
    request_header: Mapping[str, object],
    request_body: Mapping[str, object],
) -> str:
    ns = "http://schemas.xmlsoap.org/soap/envelope/"
    ET.register_namespace("soapenv", ns)
    envelope = ET.Element(f"{{{ns}}}Envelope")
    body = ET.SubElement(envelope, f"{{{ns}}}Body")
    req = ET.SubElement(body, "Request")
    rh = ET.SubElement(req, "RequestHeader")
    _append_children(rh, _REQUEST_HEADER_KEYS, request_header)
    rb = ET.SubElement(req, "RequestBody")
    _append_children(rb, _REQUEST_BODY_KEYS, request_body)
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True).decode("utf-8")


async def _process_doc_ocr(
    *,
    ctx: AgentContext,
    req: DocOCRReq,
    client: DocOCRClient,
    token: str,
    callback_url: str,
    callback_timeout: float,
    callback_max_retries: int,
    callback_base_delay: float,
) -> None:
    tracker = ctx.tracker
    request_id = ctx.request_id
    trace_id = ctx.request.headers.get("X-Trace-Id")
    logger = ctx.logger
    cfg = ctx.settings
    agent_cfg = ctx.agent_config or {}
    mock_enabled = bool(agent_cfg.get("mock_enabled", False))

    status = "FAILED"
    result = None
    error = None

    try:
        tracker.set_status(request_id, status="RUNNING", result=None, error=None, ttl=cfg.JOB_TTL_SEC)
        logger.info({"event": "doc_ocr.running", "request_id": request_id, "trace_id": trace_id})

        file_refs = list(req.files)

        used_filenames: set[str] = set()
        staged_files = []
        for idx, file_ref in enumerate(file_refs):
            filename = file_ref.filename
            if filename:
                filename = Path(filename).name
            if not filename:
                parsed_name = Path(urlsplit(file_ref.url or "").path).name
                filename = parsed_name or f"input-{idx + 1}.bin"
            if filename in used_filenames:
                stem = Path(filename).stem or "input"
                suffix = Path(filename).suffix
                filename = f"{stem}-{idx + 1}{suffix}"
            used_filenames.add(filename)

            logger.info(
                {
                    "event": "doc_ocr.download_start",
                    "request_id": request_id,
                    "idx": idx,
                    "url": file_ref.url,
                    "filename": filename,
                }
            )
            try:
                staged = await download_to_staging(
                    request_id=request_id,
                    url=file_ref.url or "",
                    staging_dir=cfg.STAGING_DIR,
                    filename=filename,
                    timeout=cfg.STAGING_DOWNLOAD_TIMEOUT_SEC,
                )
            except Exception as exc:
                error = f"download_failed: {exc}"
                logger.error(
                    {
                        "event": "doc_ocr.download_failed",
                        "request_id": request_id,
                        "idx": idx,
                        "url": file_ref.url,
                        "error": str(exc),
                    }
                )
                tracker.set_status(
                    request_id,
                    status="FAILED",
                    result=None,
                    error=error,
                    ttl=cfg.JOB_TTL_SEC,
                )
                status = "FAILED"
                return

            logger.info(
                {
                    "event": "doc_ocr.download_done",
                    "request_id": request_id,
                    "idx": idx,
                    "local_path": staged.local_path,
                    "size_bytes": staged.size_bytes,
                }
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
        if mock_enabled:
            if len(local_paths) == 1:
                agent_res = await client.run_doc_ocr_mock(local_file_path=local_paths[0], options=req.options)
            else:
                agent_res = await client.run_doc_ocr_mock_many(local_file_paths=local_paths, options=req.options)
        elif use_real:
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
            error = agent_res.error or "agent upstream error"
            tracker.set_status(
                request_id,
                status="FAILED",
                result=None,
                error=error,
                ttl=cfg.JOB_TTL_SEC,
            )
            logger.error(
                {
                    "event": "doc_ocr.failed",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "error": error,
                }
            )
            status = "FAILED"
            return

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
            if file_ref.url:
                server_path, _ = split_url_for_esb(file_ref.url)
            else:
                server_path = cfg.ESB_BASE_URL.rstrip("/")
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
        upload_subdir = "doc_ocr"
        upload_filename = f"{upload_subdir}/{request_id}-result.json"
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
        except Exception as exc:
            error = f"upload_failed: {exc}"
            tracker.set_status(
                request_id,
                status="FAILED",
                result=None,
                error=error,
                ttl=cfg.JOB_TTL_SEC,
            )
            logger.error(
                {
                    "event": "doc_ocr.upload_failed",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "error": str(exc),
                }
            )
            status = "FAILED"
            return

        result["esb_upload"] = {"server_path": primary_server_path, "server_file": upload_filename}
        tracker.set_status(request_id, status="SUCCEEDED", result=result, error=None, ttl=cfg.JOB_TTL_SEC)
        logger.info({"event": "doc_ocr.succeeded", "request_id": request_id, "trace_id": trace_id})
        status = "SUCCEEDED"
    except Exception as exc:
        error = str(exc)
        tracker.set_status(
            request_id,
            status="FAILED",
            result=None,
            error=error,
            ttl=cfg.JOB_TTL_SEC,
        )
        logger.exception({"event": "doc_ocr.unhandled_failed", "request_id": request_id, "error": error})
        status = "FAILED"
    finally:
        result_flag = 1 if status == "SUCCEEDED" else 2
        raw_headers = agent_cfg.get("headers", {}) or {}
        callback_headers = {
            str(k): str(v) for k, v in raw_headers.items() if v is not None and v != ""
        }
        callback_payload = {
            "BusinessSerlNo": request_id,
            "ResultFlag": result_flag,
            "FilePathAddr": result["esb_upload"]["server_file"] if status == "SUCCEEDED" and result else "",
            "FailReason": error or "",
        }
        now = datetime.now()
        channel_value = ""
        if isinstance(raw_headers, dict):
            channel_value = str(raw_headers.get("channel", "")).strip()
        request_header = {
            "ChanlNo": channel_value,
            "ReqSeqNo": trace_id or request_id,
            "ReqTime": now.strftime("%H%M%S"),
            "ReqDate": now.strftime("%Y%m%d"),
        }
        callback_xml_body = _build_doc_ocr_callback_xml(
            request_header=request_header,
            request_body=callback_payload,
        )
        callback_info = await send_callback(
            callback_url=callback_url,
            payload=callback_payload,
            body=callback_xml_body,
            content_type="text/xml; charset=utf-8",
            headers=callback_headers or None,
            timeout=callback_timeout,
            max_retries=callback_max_retries,
            base_delay=callback_base_delay,
            logger=logger,
            request_id=request_id,
            trace_id=trace_id,
        )
        if status == "SUCCEEDED" and isinstance(result, dict):
            result["callback"] = callback_info
            tracker.set_status(
                request_id,
                status=status,
                result=result,
                error=error,
                ttl=cfg.JOB_TTL_SEC,
            )
        tracker.release_lock(request_id, token)


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
        mock_result_path=_cfg("mock_result_file"),
    )

    callback_url = _cfg("callback_url") or cfg.DOC_OCR_CALLBACK_URL
    queue = ctx.request.app.state.doc_ocr_queue

    logger.info({"event": "doc_ocr.received", "request_id": request_id, "trace_id": trace_id})

    logger.info({"event": "doc_ocr.check_existing", "request_id": request_id})
    _, existing = tracker.get_job(request_id)
    if existing:
        return DocOCRResp(
            request_id=request_id,
            status=existing.get("status", "UNKNOWN"),
            result=existing.get("result"),
            error=existing.get("error"),
        )

    logger.info(
        {"event": "doc_ocr.acquire_lock", "request_id": request_id, "ttl": cfg.IDEMPOTENCY_TTL_SEC}
    )
    token, _ = tracker.acquire_lock(request_id, ttl=cfg.IDEMPOTENCY_TTL_SEC)
    if not token:
        logger.info({"event": "doc_ocr.lock_busy", "request_id": request_id})
        return DocOCRResp(request_id=request_id, status="RUNNING")

    tracker.set_status(request_id, status="QUEUED", result=None, error=None, ttl=cfg.JOB_TTL_SEC)

    def _job_factory():
        return _process_doc_ocr(
            ctx=ctx,
            req=req,
            client=client,
            token=token,
            callback_url=callback_url,
            callback_timeout=cfg.DOC_OCR_CALLBACK_TIMEOUT_SEC,
            callback_max_retries=cfg.DOC_OCR_CALLBACK_MAX_RETRIES,
            callback_base_delay=cfg.DOC_OCR_CALLBACK_BASE_DELAY_SEC,
        )

    accepted = queue.try_enqueue_nowait(request_id, _job_factory)
    if not accepted:
        error = "queue_full"
        tracker.set_status(request_id, status="REJECTED", result=None, error=error, ttl=cfg.JOB_TTL_SEC)
        tracker.release_lock(request_id, token)
        logger.warning({"event": "doc_ocr.rejected_queue_full", "request_id": request_id, "trace_id": trace_id})
        return JSONResponse(
            status_code=429,
            content=DocOCRResp(request_id=request_id, status="REJECTED", error=error).model_dump(exclude_none=True),
        )

    logger.info({"event": "doc_ocr.queued", "request_id": request_id, "trace_id": trace_id})

    return JSONResponse(
        status_code=202,
        content=DocOCRResp(request_id=request_id, status="QUEUED").model_dump(exclude_none=True),
    )
