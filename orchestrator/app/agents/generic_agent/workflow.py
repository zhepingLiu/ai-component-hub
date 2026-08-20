from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from ...config import settings
from ...core.context import AgentExecutionContext
from ...services.file_stage import StagedFile, download_to_staging
from .client import GenericAgentClient
from .schema import GenericAgentReq


def build_generic_agent_client(agent_cfg: dict) -> GenericAgentClient:
    def _cfg(*keys: str) -> str:
        query = agent_cfg.get("query", {}) if isinstance(agent_cfg.get("query"), dict) else {}
        headers = agent_cfg.get("headers", {}) if isinstance(agent_cfg.get("headers"), dict) else {}
        for key in keys:
            value = agent_cfg.get(key)
            if isinstance(value, str) and value:
                return value
            value = query.get(key)
            if isinstance(value, str) and value:
                return value
            value = headers.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    timeout_value = agent_cfg.get("timeout_sec") or agent_cfg.get("request_timeout_sec") or 120.0
    try:
        timeout = float(timeout_value)
    except (TypeError, ValueError):
        timeout = 120.0

    return GenericAgentClient(
        base_url=_cfg("base_url", "host"),
        conversation_url=_cfg("conversation_url"),
        upload_url=_cfg("upload_url"),
        run_url=_cfg("run_url"),
        authorization=_cfg("api_key", "authorization"),
        access_key=_cfg("access_key", "ak", "Access-Key") or settings.AB_ACCESS_KEY,
        secret_key=_cfg("secret_key", "sk", "Secret-Key") or settings.AB_SECRET_KEY,
        x_authorization=_cfg("x_authorization", "X-Authorization"),
        app_id=_cfg("app_id", "appId"),
        department_id=_cfg("department_id", "departmentId"),
        timeout=timeout,
    )


async def stage_generic_agent_files(*, ctx: AgentExecutionContext, req: GenericAgentReq) -> list[StagedFile]:
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
                "event": "generic_agent.download_start",
                "agent": ctx.agent_name,
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
                    "event": "generic_agent.download_failed",
                    "agent": ctx.agent_name,
                    "request_id": ctx.request_id,
                    "idx": idx,
                    "url": file_ref.url,
                    "error": str(exc),
                }
            )
            raise RuntimeError(f"download_failed: {exc}") from exc
        ctx.logger.info(
            {
                "event": "generic_agent.download_done",
                "agent": ctx.agent_name,
                "request_id": ctx.request_id,
                "idx": idx,
                "local_path": staged.local_path,
                "size_bytes": staged.size_bytes,
            }
        )
        staged_files.append(staged)
    return staged_files
