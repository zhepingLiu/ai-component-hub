from pydantic import BaseModel

class UploadReq(BaseModel):
    server_path: str
    server_file: str
    local_file_path: str

class DownloadReq(BaseModel):
    server_path: str
    server_file: str
    local_file_path: str
    