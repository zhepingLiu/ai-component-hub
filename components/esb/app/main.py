import logging
import os
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, JSONResponse

from app.schemas import UploadReq, DownloadReq
from app.logging_utils import setup_logging

LOG_DIR = os.getenv("LOG_DIR", "/app/data/logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "10"))

setup_logging("esb", LOG_DIR, LOG_LEVEL, LOG_RETENTION_DAYS)
logger = logging.getLogger("esb")

app = FastAPI()

# 固定配置（你的 ESB 文件服务器地址）
SERVER_BASE_URL = "http://fserver.sit.cqrcb.com:21014"
TIMEOUT = 60  # seconds
APPSOURCE = "CQRCB_ESBFILE_SOURCE"


# ----------------------------------------------------
#  ESB DOWNLOAD (下载到 Docker 本地)
# ----------------------------------------------------
@app.post("/esb-download")
async def esb_download(req: DownloadReq):
    server_path = req.server_path
    server_file = req.server_file
    local_file_path = req.local_file_path

    if not server_path or not server_file:
        logger.warning({"event": "esb.download.invalid", "server_path": server_path, "server_file": server_file})
        return JSONResponse(content=False, status_code=400)

    # 拼接 URL
    server_path = server_path.rstrip("/")
    server_file = server_file.lstrip("/")
    url = f"{server_path}/{server_file}"

    async def _stream() -> AsyncIterator[bytes]:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            yield chunk
        except Exception as e:
            logger.error({"event": "esb.download.failed", "url": url, "error": str(e)})
            # 触发 FastAPI 重新抛出异常，返回 502
            raise

    # 如果带 local_file_path，就直接写到容器文件系统；否则以流方式返回
    if local_file_path:
        try:
            os.makedirs(os.path.dirname(local_file_path) or ".", exist_ok=True)
            with open(local_file_path, "wb") as f:
                async for chunk in _stream():
                    f.write(chunk)
        except Exception as e:
            logger.error(
                {
                    "event": "esb.download.write_failed",
                    "local_file_path": local_file_path,
                    "error": str(e),
                }
            )
            return JSONResponse(content=False)

        logger.info({"event": "esb.download.saved", "url": url, "local_file_path": local_file_path})
        return JSONResponse(content=True)

    logger.info({"event": "esb.download.stream", "url": url})
    return StreamingResponse(_stream(), media_type="application/octet-stream")


# ----------------------------------------------------
#  ESB UPLOAD（从 Docker 本地读取文件 → 上传）
# ----------------------------------------------------
@app.post("/esb-upload")
async def esb_upload(req: UploadReq):
    server_path = req.server_path
    server_file = req.server_file
    local_file_path = req.local_file_path

    if not server_path or not server_file or not local_file_path:
        logger.warning(
            {
                "event": "esb.upload.invalid",
                "server_path": server_path,
                "server_file": server_file,
                "local_file_path": local_file_path,
            }
        )
        return JSONResponse(content=False)

    # 检查容器内部文件是否存在
    if not os.path.exists(local_file_path):
        logger.warning({"event": "esb.upload.missing_local", "local_file_path": local_file_path})
        return JSONResponse(content=False)

    # 读取本地文件
    try:
        with open(local_file_path, "rb") as f:
            file_bytes = f.read()
    except Exception as e:
        logger.error({"event": "esb.upload.read_failed", "local_file_path": local_file_path, "error": str(e)})
        return JSONResponse(content=False)

    # ESB 上传地址
    server_path = server_path.rstrip("/")
    url = f"{server_path}/upload"

    files = {
        "file": (server_file, file_bytes, "application/octet-stream")
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, files=files)
            resp.raise_for_status()
    except Exception as e:
        logger.error({"event": "esb.upload.failed", "url": url, "error": str(e)})
        return JSONResponse(content=False)

    logger.info({"event": "esb.upload.succeeded", "server_path": server_path, "server_file": server_file})
    return JSONResponse(content=True)
