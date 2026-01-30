from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, model_validator


class FileRef(BaseModel):
    url: Optional[str] = Field(None, description="Optional HTTP file url on file server")
    filename: Optional[str] = Field(
        None, description="File path or filename. Prefer relative path like /path/to/file.pdf"
    )

    @model_validator(mode="after")
    def _ensure_ref(self) -> "FileRef":
        if not self.url and not self.filename:
            raise ValueError("Either 'url' or 'filename' must be provided")
        return self


class DocOCRReq(BaseModel):
    request_id: Optional[str] = Field(None, description="Idempotency key. If absent, server will generate one.")
    files: list[FileRef] = Field(default_factory=list)
    options: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ensure_files(self) -> "DocOCRReq":
        if not self.files:
            raise ValueError("'files' must be provided")
        return self


class DocOCRResp(BaseModel):
    request_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
