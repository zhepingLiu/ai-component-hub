from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ...batch.models import BatchOptions


class KehutongInputs(BaseModel):
    customer_list_request: dict[str, Any] = Field(default_factory=dict)
    customer_data_request: dict[str, Any] = Field(default_factory=dict)
    agent_inputs: dict[str, Any] = Field(default_factory=dict)


class KehutongOptions(BaseModel):
    batch: BatchOptions = Field(default_factory=BatchOptions)
    scheduled_at: datetime | None = None
    max_customers: int = Field(default=10000, ge=1, le=100000)
    deduplicate_customer_ids: bool = True
    fail_on_any_task_error: bool = False
    agent_options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scheduled_at")
    @classmethod
    def scheduled_at_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled_at must include a timezone offset")
        return value


class KehutongSecretaryReq(BaseModel):
    request_id: str | None = None
    inputs: KehutongInputs = Field(default_factory=KehutongInputs)
    options: KehutongOptions = Field(default_factory=KehutongOptions)


class KehutongSecretaryResp(BaseModel):
    request_id: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None


class ResponsePathConfig(BaseModel):
    customer_ids_path: str | None = None
    customer_data_path: str | None = None


class CustomerIdBinding(BaseModel):
    location: Literal["body", "query", "path"] = "body"
    field: str = "customerId"


class HttpApiConfig(BaseModel):
    url: str = Field(min_length=1)
    method: Literal["GET", "POST"] = "POST"
    static_body: dict[str, Any] = Field(default_factory=dict)
    response: ResponsePathConfig = Field(default_factory=ResponsePathConfig)
    customer_id: CustomerIdBinding | None = None


class CustomerProviderConfig(BaseModel):
    type: Literal["http"] = "http"
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    headers: dict[str, str] = Field(default_factory=dict)
    retryable_status_codes: set[int] = Field(
        default_factory=lambda: {408, 429, 500, 502, 503, 504}
    )
    customer_list_api: HttpApiConfig
    customer_data_api: HttpApiConfig


class KehutongBatchPayload(BaseModel):
    inputs: KehutongInputs = Field(default_factory=KehutongInputs)
    options: KehutongOptions = Field(default_factory=KehutongOptions)
