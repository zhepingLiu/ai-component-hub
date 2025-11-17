import os
import time
import requests

import http.client as http_client
import logging

from fastapi import FastAPI
from .schemas import UploadReq, DownloadReq

app = FastAPI(title="esb")

TIMEOUT = 60  # seconds
APPSOURCE = "CQRCB_ESBFILE_SOURCE"
SERVER_PATH = "http://fserver.sit.cqrcb.com:21014"

@app.get("/health")
def health():
    return "ok"

@app.get("/esb-download")
def esb_download(req: DownloadReq) -> None:
    server_path = SERVER_PATH
    server_file = req.server_file
    local_file = req.local_file
    
    if not server_file or not local_file:
        print("文件名不能为空")
        return
    
    if server_path.endswith("/"):
        server_path = server_path[:server_path.rfind("/")]
        
    server_file = server_path + server_file
    
    try:
        response = requests.get(server_file, stream=True, timeout=TIMEOUT)
        response.raise_for_status()
        
        if os.path.exists(local_file):
            os.remove(local_file)
            
        with open(local_file, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)

        print("文件下载成功")
        
    except requests.exceptions.RequestException as e:
        print(f"文件下载失败: {e}")
        raise RuntimeError(f"文件下载失败: {e}")

@app.get("/esb-upload")
def esb_upload(req: UploadReq) -> bool:
    appSource = APPSOURCE
    server_path = SERVER_PATH
    server_file_name = req.server_file
    local_file_path = req.local_file
    
    if not server_path or not server_file_name or not local_file_path:
        print("参数不能为空")
        return False
    
    if not os.path.exists(local_file_path):
        print("本地文件不存在")
        return False
    
    if not server_path.endswith("/"):
        server_path = server_path[:server_path.rfind("/")]
        
    start_tm = int(time.time()*1000)
    print(f"开始上传文件: {local_file_path} 到 {server_path}{server_file_name}，时间: {start_tm}ms")
    
    s_boundary = f"---------7dcd52d09f4{start_tm}---------"
    prefix = (
        f"--{s_boundary}\r\n"
        f"Content-Disposition: form-data; name=\"{appSource}; filename=\"{server_file_name}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    
    suffix = f"\r\n--{s_boundary}--\r\n".encode("utf-8")
    
    file_size = os.path.getsize(local_file_path)
    content_length = len(prefix) + file_size + len(suffix)
    
    headers = {
        "Pragma": "XMLMD5",
        "Content-Type": f"multipart/form-data; boundary={s_boundary}",
    }
        
    try:
        with open(local_file_path, "rb") as fp:
            body = prefix + fp.read() + suffix
            
        auth = ('esb', "1qa2ws#ED")
        
        resp = requests.post(server_path, data=body, headers=headers, auth=auth, timeout=TIMEOUT)
        
        end_tm = int(time.time()*1000)
        print(f"上传文件结束，时间: {end_tm - start_tm}ms,")
        print(f"文件大小: {file_size} bytes")
        
        if resp.status_code == 200:
            print("文件上传成功")
            return True
        else:
            print(f"文件上传失败, 状态码: {resp.status_code}, 响应内容: {resp.text}")
            return False
        
    except requests.exceptions.RequestException as e:
        print(f"文件上传失败: {e}")
        return False
    