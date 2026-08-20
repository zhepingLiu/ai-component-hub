from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .models import BatchOptions, BatchRecord, TaskRecord


class CreateBatchRequest(BaseModel):
    batch_type: str = Field(min_length=1, max_length=100)
    input: dict[str, Any] = Field(default_factory=dict)
    options: BatchOptions | None = None
    scheduled_at: datetime | None = None

    @field_validator("scheduled_at")
    @classmethod
    def scheduled_at_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled_at must include a timezone offset")
        return value


class CreateBatchResponse(BaseModel):
    batch_id: str
    status: str
    created: bool


class BatchListResponse(BaseModel):
    items: list[BatchRecord]
    next_cursor: int | None = None


class TaskListResponse(BaseModel):
    items: list[TaskRecord]
    next_cursor: int | None = None


class RescheduleBatchRequest(BaseModel):
    scheduled_at: datetime

    @field_validator("scheduled_at")
    @classmethod
    def scheduled_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must include a timezone offset")
        return value
