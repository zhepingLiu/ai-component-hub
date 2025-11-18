from fastapi import FastAPI
from fastapi.responses import StreamingResponse, JSONResponse
from app.schemas import UploadReq, DownloadReq
import httpx
import os

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

    if not server_path or not server_file or not local_file_path:
        print("参数不能为空")
        return JSONResponse(content=False)

    # 拼接 URL
    server_path = server_path.rstrip("/")
    server_file = server_file.lstrip("/")
    url = f"{server_path}/{server_file}"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content  # 保持你原来的简单逻辑
    except Exception as e:
        print("下载失败:", e)
        return JSONResponse(content=False)

    # 写入 Docker 本地
    try:
        with open(local_file_path, "wb") as f:
            f.write(content)
    except Exception as e:
        print("写入本地文件失败:", e)
        return JSONResponse(content=False)

    return JSONResponse(content=True)


# ----------------------------------------------------
#  ESB UPLOAD（从 Docker 本地读取文件 → 上传）
# ----------------------------------------------------
@app.post("/esb-upload")
async def esb_upload(req: UploadReq):
    server_path = req.server_path
    server_file = req.server_file
    local_file_path = req.local_file_path

    if not server_path or not server_file or not local_file_path:
        print("参数不能为空")
        return JSONResponse(content=False)

    # 检查容器内部文件是否存在
    if not os.path.exists(local_file_path):
        print("本地文件不存在:", local_file_path)
        return JSONResponse(content=False)

    # 读取本地文件
    try:
        with open(local_file_path, "rb") as f:
            file_bytes = f.read()
    except Exception as e:
        print("读取本地文件失败:", e)
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
        print("上传失败:", e)
        return JSONResponse(content=False)

    return JSONResponse(content=True)
