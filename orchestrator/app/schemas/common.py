from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class AgentStatusResp(BaseModel):
    request_id: str
    status: str
    result: Optional[Any] = None
    error: Optional[str] = None


class AgentResult(BaseModel):
    ok: bool
    data: dict[str, Any]
    error: Optional[str] = None
