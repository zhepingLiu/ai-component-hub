from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx

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

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
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
