from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from ..config import Settings


@dataclass(frozen=True)
class AgentContext:
    request: Request
    settings: Settings
    tracker: Any
    request_id: str
    agent_name: str
    agent_config: dict[str, Any]
    json_body: dict[str, Any] | None
    raw_body: bytes
    logger: Any
