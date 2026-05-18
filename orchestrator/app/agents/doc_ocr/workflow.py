from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from ...core.context import AgentExecutionContext
from ...schemas.common import AgentResult
from ...services.file_stage import StagedFile, download_to_staging
from .client import DocOCRClient
from .schema import DocOCRReq


def build_doc_ocr_client(agent_cfg: dict) -> DocOCRClient:
    def _cfg(*keys: str) -> str:
        for key in keys:
            value = agent_cfg.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    return DocOCRClient(
        base_url=_cfg("base_url", "host"),
        conversation_url=_cfg("conversation_url"),
        upload_url=_cfg("upload_url"),
        run_url=_cfg("run_url"),
        authorization=_cfg("authorization", "private_key", "secret"),
        app_id=_cfg("app_id", "appId"),
        department_id=_cfg("department_id", "departmentId"),
        mock_result_path=_cfg("mock_result_file"),
    )


async def stage_doc_ocr_files(*, ctx: AgentExecutionContext, req: DocOCRReq) -> list[StagedFile]:
    used_filenames: set[str] = set()
    staged_files = []
    for idx, file_ref in enumerate(req.files):
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

        ctx.logger.info(
            {
                "event": "doc_ocr.download_start",
                "request_id": ctx.request_id,
                "idx": idx,
                "url": file_ref.url,
                "filename": filename,
            }
        )
        try:
            staged = await download_to_staging(
                request_id=ctx.request_id,
                url=file_ref.url or "",
                staging_dir=ctx.settings.STAGING_DIR,
                filename=filename,
                timeout=ctx.settings.STAGING_DOWNLOAD_TIMEOUT_SEC,
            )
        except Exception as exc:
            ctx.logger.error(
                {
                    "event": "doc_ocr.download_failed",
                    "request_id": ctx.request_id,
                    "idx": idx,
                    "url": file_ref.url,
                    "error": str(exc),
                }
            )
            raise RuntimeError(f"download_failed: {exc}") from exc
        ctx.logger.info(
            {
                "event": "doc_ocr.download_done",
                "request_id": ctx.request_id,
                "idx": idx,
                "local_path": staged.local_path,
                "size_bytes": staged.size_bytes,
            }
        )
        staged_files.append(staged)
    return staged_files


async def run_doc_ocr_client(
    *,
    client: DocOCRClient,
    agent_cfg: dict,
    req: DocOCRReq,
    local_paths: list[str],
) -> AgentResult:
    mock_enabled = bool(agent_cfg.get("mock_enabled", False))
    use_real = agent_cfg.get("use_real", False) or bool(
        agent_cfg.get("base_url")
        or agent_cfg.get("host")
        or agent_cfg.get("conversation_url")
        or agent_cfg.get("upload_url")
        or agent_cfg.get("run_url")
        or agent_cfg.get("app_id")
        or agent_cfg.get("appId")
    )

    if mock_enabled:
        if len(local_paths) == 1:
            return await client.run_doc_ocr_mock(local_file_path=local_paths[0], options=req.options)
        return await client.run_doc_ocr_mock_many(local_file_paths=local_paths, options=req.options)

    if use_real:
        if len(local_paths) == 1:
            return await client.run_doc_ocr_real(local_file_path=local_paths[0], options=req.options)
        return await client.run_doc_ocr_real_many(local_file_paths=local_paths, options=req.options)

    if len(local_paths) == 1:
        return await client.run_doc_ocr(local_file_path=local_paths[0], options=req.options)
    return await client.run_doc_ocr_many(local_file_paths=local_paths, options=req.options)
