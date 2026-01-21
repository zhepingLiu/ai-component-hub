from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, model_validator


class FileRef(BaseModel):
    url: str = Field(..., description="HTTP file url on file server")
    filename: Optional[str] = Field(None, description="Optional local filename for staging")


class DocOCRReq(BaseModel):
    request_id: Optional[str] = Field(None, description="Idempotency key. If absent, server will generate one.")
    file: Optional[FileRef] = None
    files: list[FileRef] = Field(default_factory=list)
    options: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ensure_files(self) -> "DocOCRReq":
        if not self.file and not self.files:
            raise ValueError("Either 'file' or 'files' must be provided")
        return self


class DocOCRResp(BaseModel):
    request_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
