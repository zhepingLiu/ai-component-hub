from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TaskResultKind(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


@dataclass(frozen=True)
class TaskExecutionResult:
    kind: TaskResultKind
    output: dict[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None
    retry_after_seconds: int | None = None

    @classmethod
    def succeeded(cls, output: dict[str, Any] | None = None) -> "TaskExecutionResult":
        return cls(kind=TaskResultKind.SUCCEEDED, output=output)

    @classmethod
    def retryable_failure(
        cls, error_code: str, error: str, *, retry_after_seconds: int | None = None
    ) -> "TaskExecutionResult":
        return cls(
            kind=TaskResultKind.RETRYABLE_FAILURE,
            error_code=error_code,
            error=error,
            retry_after_seconds=retry_after_seconds,
        )

    @classmethod
    def permanent_failure(cls, error_code: str, error: str) -> "TaskExecutionResult":
        return cls(kind=TaskResultKind.PERMANENT_FAILURE, error_code=error_code, error=error)
