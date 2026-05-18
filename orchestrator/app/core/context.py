from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import Settings


@dataclass(frozen=True)
class AgentExecutionContext:
    request_id: str
    trace_id: str | None
    agent_name: str
    agent_config: dict[str, Any]
    settings: Settings
    tracker: Any
    logger: Any
