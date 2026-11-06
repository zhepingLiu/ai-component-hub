from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...batch.models import BatchOptions


def _shanghai_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


class CustomerDataRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    dataDate: date = Field(default_factory=_shanghai_today)


class CustomerListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_id: int = Field(default=0, ge=0, strict=True)
    pageSize: int = Field(default=50, ge=1, le=2000)
    cust_no: str | None = None


class KehutongInputs(BaseModel):
    customer_list_request: CustomerListRequest = Field(default_factory=CustomerListRequest)
    customer_data_request: CustomerDataRequest = Field(default_factory=CustomerDataRequest)


class KehutongOptions(BaseModel):
    batch: BatchOptions = Field(default_factory=BatchOptions)
    scheduled_at: datetime | None = None
    groups_per_request: int = Field(default=1, ge=1, strict=True)
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


class HttpApiConfig(BaseModel):
    url: str = Field(min_length=1)
    method: Literal["GET", "POST"] = "POST"
    static_body: dict[str, Any] = Field(default_factory=dict)
    response: ResponsePathConfig = Field(default_factory=ResponsePathConfig)


class CustomerProviderConfig(BaseModel):
    type: Literal["http"] = "http"
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    headers: dict[str, str] = Field(default_factory=dict)
    retryable_status_codes: set[int] = Field(
        default_factory=lambda: {408, 429, 500, 502, 503, 504}
    )
    customer_list_api: HttpApiConfig
    customer_relations_api: HttpApiConfig
    chat_messages_api: HttpApiConfig
    follow_plan_api: HttpApiConfig
    customer_dynamic_api: HttpApiConfig
    customer_briefing_api: HttpApiConfig


class KehutongBatchPayload(BaseModel):
    inputs: KehutongInputs = Field(default_factory=KehutongInputs)
    options: KehutongOptions = Field(default_factory=KehutongOptions)
