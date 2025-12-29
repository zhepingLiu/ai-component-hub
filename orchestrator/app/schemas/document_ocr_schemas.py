from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class FileRef(BaseModel):
    # 你们文件服务器是 HTTP 下载；这里用“完整URL”最简单
    url: str = Field(..., description="HTTP file url on file server")
    filename: Optional[str] = Field(None, description="Optional local filename for staging")


class DocOCRReq(BaseModel):
    request_id: Optional[str] = Field(None, description="Idempotency key. If absent, server will generate one.")
    file: FileRef
    options: Dict[str, Any] = Field(default_factory=dict)


class DocOCRResp(BaseModel):
    request_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
