from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BatchAgentEnvelope(BaseModel):
    request_id: str
    trace_id: str | None = None
    agent_name: str
    fail_on_any_task_error: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)
