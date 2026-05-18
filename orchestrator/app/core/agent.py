from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .context import AgentExecutionContext
from .result import AgentExecutionResult


class BaseAgent(ABC):
    name: str
    log_event_prefix: str | None = None

    @abstractmethod
    async def validate_request(self, payload: dict[str, Any]) -> Any:
        """Validate raw request payload and return an agent-specific request object."""

    @abstractmethod
    async def execute(self, ctx: AgentExecutionContext, req: Any) -> AgentExecutionResult:
        """Run the agent-specific workflow."""

    async def postprocess(
        self,
        ctx: AgentExecutionContext,
        req: Any,
        result: AgentExecutionResult,
    ) -> AgentExecutionResult:
        """Optional delivery/callback step after execute()."""
        return result
