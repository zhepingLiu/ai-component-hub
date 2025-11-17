from pydantic import BaseModel

class UploadReq(BaseModel):
    server_file: str
    local_file: str
    
class DownloadReq(BaseModel):
    server_file: str
    local_file: str

class StdResp(BaseModel):
    code: int = 0
    message: str = "ok"
    data: dict | list | None = None
    