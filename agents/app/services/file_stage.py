from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..config import settings


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
    从 HTTP 文件服务器下载到 staging 目录（外部 volume 挂载路径）。
    - 采用流式下载，避免大文件读入内存
    - 计算 sha256 便于审计/排障
    """
    base = Path(staging_dir) / request_id
    base.mkdir(parents=True, exist_ok=True)

    dst = base / filename

    size = 0
    h = hashlib.sha256()

    server_path, server_file = split_url_for_esb(url)
    esb_endpoint = settings.ESB_BASE_URL.rstrip("/") + "/esb-download"
    payload = {
        "server_path": server_path,
        "server_file": server_file,
        # 让 ESB 以流方式返回内容，由 agents 写入 staging
        "local_file_path": None,
    }

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("POST", esb_endpoint, json=payload) as resp:
            resp.raise_for_status()
            with dst.open("wb") as f:
                async for chunk in resp.aiter_bytes():
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
    将 JSON 内容写入本地临时文件后，通过 ESB 服务上传到文件服务器。
    说明：ESB 的 /esb-upload 接口要求容器内存在待上传文件。
    """
    esb_endpoint = settings.ESB_BASE_URL.rstrip("/") + "/esb-upload"

    # 在指定路径写入文件（需确保 ESB 容器可访问该路径，建议挂载共享卷）
    tmp_file = Path(local_file_path) if local_file_path else Path("/tmp/esb_uploads") / server_file
    tmp_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    body = {
        "server_path": server_path,
        "server_file": server_file,
        "local_file_path": str(tmp_file),
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(esb_endpoint, json=body)
        resp.raise_for_status()
        ok = False
        try:
            ok = bool(resp.json())
        except Exception:
            ok = False
        if not ok:
            raise RuntimeError(f"ESB upload failed, response: {resp.text}")
