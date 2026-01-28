from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..config import settings

logger = logging.getLogger("orchestrator.file_stage")

@dataclass
class StagedFile:
    request_id: str
    url: str
    local_path: str
    size_bytes: int
    sha256: str


async def download_to_staging(
    *,
    request_id: str,
    url: str,
    staging_dir: str,
    filename: str = "input.bin",
    timeout: float | None = 60.0,
) -> StagedFile:
    """
    从 HTTP 文件服务器下载到 staging 目录（容器内可写路径）。
    - 采用流式下载，避免大文件读入内存
    - 计算 sha256 便于审计/排障
    """
    base = Path(staging_dir) / request_id
    base.mkdir(parents=True, exist_ok=True)

    dst = base / filename

    size = 0
    h = hashlib.sha256()

    download_url = url or (settings.ESB_BASE_URL.rstrip("/") + "/SSC")

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", download_url) as resp:
            resp.raise_for_status()
            with dst.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    size += len(chunk)
                    h.update(chunk)

    return StagedFile(
        request_id=request_id,
        url=url,
        local_path=str(dst),
        size_bytes=size,
        sha256=h.hexdigest(),
    )


def split_url_for_esb(file_url: str) -> tuple[str, str]:
    parsed = urlsplit(file_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid file url: {file_url}")

    dir_path, _, filename = parsed.path.rpartition("/")
    if not filename:
        raise ValueError(f"File url missing filename: {file_url}")

    server_path = f"{parsed.scheme}://{parsed.netloc}{dir_path}"
    return server_path, filename


async def upload_json_via_esb(
    *,
    server_path: str,
    server_file: str,
    payload: dict,
    local_file_path: str | None = None,
    timeout: float | None = 60.0,
) -> None:
    """
    读取本地文件后，通过文件服务器接口上传。
    说明：按现有文件服务器要求拼 multipart，并包含 Pragma 头。
    """
    if not local_file_path:
        raise RuntimeError("Upload requires local_file_path")

    local_path = Path(local_file_path)
    if not local_path.exists():
        raise RuntimeError(f"Local upload file missing: {local_file_path}")

    file_bytes = local_path.read_bytes()
    upload_url = settings.ESB_BASE_URL.rstrip("/")
    if not upload_url:
        server_path = server_path.rstrip("/")
        upload_url = server_path if server_path.endswith("/upload") else f"{server_path}/upload"
    else:
        upload_url = f"{upload_url}/upload.do"

    async with httpx.AsyncClient(timeout=timeout) as client:
        start_tm = int(time.time() * 1000)
        boundary = f"----------7dcd52d09f4{start_tm}----------"
        prefix = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{settings.FILE_SERVER_APPSOURCE}"; '
            f'filename="{server_file}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf8")
        suffix = f"\r\n--{boundary}--\r\n".encode("utf8")
        body = prefix + file_bytes + suffix
        headers = {
            "Pragma": "XMLMD5",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        resp = await client.post(upload_url, headers=headers, content=body)
        resp.raise_for_status()
        logger.info(
            {
                "event": "file_stage.upload_response",
                "status_code": resp.status_code,
                "content_type": resp.headers.get("content-type"),
                "text": resp.text[:500],
            }
        )
        ok = 200 <= resp.status_code < 300
        if not ok:
            raise RuntimeError(f"Upload failed, response: {resp.text}")
