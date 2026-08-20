from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, Field


class BatchStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    CREATED = "CREATED"
    PREPARING = "PREPARING"
    GENERATING_TASKS = "GENERATING_TASKS"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    CANCELLING = "CANCELLING"
    FINALIZING = "FINALIZING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL_FAILED = "PARTIAL_FAILED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BatchOptions(BaseModel):
    max_concurrency: int = Field(default=10, ge=1, le=100)
    max_attempts: int = Field(default=3, ge=1, le=10)
    task_timeout_seconds: int = Field(default=300, ge=10, le=86400)
    retry_delays_seconds: list[Annotated[int, Field(ge=0, le=86400)]] = Field(
        default_factory=lambda: [60, 300, 1200]
    )


class BatchRecord(BaseModel):
    batch_id: str
    batch_type: str
    definition_version: str
    status: BatchStatus
    input: dict[str, Any]
    options: BatchOptions
    runtime_context: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    scheduled_at: str | None = None
    total: int = 0
    pending: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    generation_complete: bool = False
    resume_status: BatchStatus | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class TaskRecord(BaseModel):
    task_id: str
    batch_id: str
    business_key: str
    status: TaskStatus
    payload: dict[str, Any]
    attempt: int = 0
    max_attempts: int
    lease_until: float | None = None
    worker_id: str | None = None
    output: dict[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
