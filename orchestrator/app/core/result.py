from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentExecutionResult:
    status: str
    data: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def succeeded(cls, data: dict[str, Any] | None = None) -> "AgentExecutionResult":
        return cls(status="SUCCEEDED", data=data, error=None)

    @classmethod
    def failed(cls, error: str, data: dict[str, Any] | None = None) -> "AgentExecutionResult":
        return cls(status="FAILED", data=data, error=error)


@dataclass(frozen=True)
class AgentSubmitResult:
    request_id: str
    status: str
    http_status: int = 200
    result: Any = None
    error: str | None = None
