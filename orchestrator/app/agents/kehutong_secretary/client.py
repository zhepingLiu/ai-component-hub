from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any, Protocol
from uuid import uuid4

import httpx

from .schema import CustomerProviderConfig, HttpApiConfig


class RetryableCustomerApiError(RuntimeError):
    pass


class PermanentCustomerApiError(RuntimeError):
    pass


class CustomerDataProvider(Protocol):
    async def list_customer_ids(self, request: dict[str, Any]) -> list[str]: ...

    async def get_aggregate_data(self, customer_id: str, request: dict[str, Any]) -> Any: ...


class HttpCustomerDataProvider:
    def __init__(self, config: CustomerProviderConfig):
        self.config = config

    async def list_customer_ids(self, request: dict[str, Any]) -> list[str]:
        api = self.config.customer_list_api
        payload = await self._request(
            api,
            request,
            extra_headers={"channelSystem": "AB", "traceId": str(uuid4())},
        )
        path = api.response.customer_ids_path or "data"
        customer_records = self._extract(payload, path)
        if not isinstance(customer_records, list):
            raise PermanentCustomerApiError(
                f"customer id response path '{path}' did not resolve to a list"
            )

        customer_ids: list[str] = []
        for index, record in enumerate(customer_records):
            if not isinstance(record, dict) or record.get("cust_no") is None:
                raise PermanentCustomerApiError(
                    f"customer id response item at index {index} does not contain 'cust_no'"
                )
            customer_ids.append(str(record["cust_no"]))
        return customer_ids

    async def get_aggregate_data(
        self,
        customer_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        data_dt = self._format_data_date(request.get("dataDate"))
        follow_plan, messages = await asyncio.gather(
            self._get_follow_plan(customer_id),
            self._get_all_chat_messages(customer_id, data_dt),
        )
        return {"follow_plan": follow_plan, "msgs": messages}

    async def _get_follow_plan(self, customer_id: str) -> list[Any]:
        payload = await self._request(
            self.config.follow_plan_api,
            {"target_id": customer_id},
            extra_headers=self._customer_api_headers(),
        )
        follow_plan = self._extract(payload, "data")
        if not isinstance(follow_plan, list):
            raise PermanentCustomerApiError("follow plan response 'data' is not a list")
        return follow_plan

    async def _get_all_chat_messages(
        self,
        customer_id: str,
        data_dt: str,
    ) -> dict[str, Any]:
        page_num = 1
        page_size = 2000
        all_rows: list[Any] = []
        aggregate: dict[str, Any] | None = None

        while True:
            payload = await self._request(
                self.config.chat_messages_api,
                {
                    "to_list": customer_id,
                    "from1": customer_id,
                    "data_dt": data_dt,
                    "pageNum": page_num,
                    "pageSize": page_size,
                },
                extra_headers=self._customer_api_headers(),
            )
            page = self._extract(payload, "data")
            if not isinstance(page, dict):
                raise PermanentCustomerApiError("chat response 'data' is not an object")
            rows = page.get("rows")
            if not isinstance(rows, list):
                raise PermanentCustomerApiError("chat response 'data.rows' is not a list")
            try:
                total_num = int(page["totalNum"])
            except (KeyError, TypeError, ValueError) as exc:
                raise PermanentCustomerApiError(
                    "chat response 'data.totalNum' is not a valid integer"
                ) from exc

            if aggregate is None:
                aggregate = dict(page)
            all_rows.extend(rows)
            if len(all_rows) >= total_num:
                aggregate["rows"] = all_rows
                aggregate["totalNum"] = total_num
                aggregate["pageNum"] = 1
                aggregate["pageSize"] = page_size
                return aggregate
            if not rows:
                raise PermanentCustomerApiError(
                    "chat pagination returned an empty page before totalNum was reached"
                )
            page_num += 1

    async def _request(
        self,
        api: HttpApiConfig,
        request: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        url = api.url
        body = {**api.static_body, **request}

        try:
            headers = {**self.config.headers, **(extra_headers or {})}
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                if api.method == "GET":
                    response = await client.get(
                        url,
                        params=body,
                        headers=headers,
                    )
                else:
                    response = await client.post(
                        url,
                        json=body,
                        headers=headers,
                    )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise RetryableCustomerApiError(str(exc)) from exc

        if response.status_code in self.config.retryable_status_codes:
            raise RetryableCustomerApiError(
                f"customer API returned HTTP {response.status_code}: {response.text[:500]}"
            )
        if response.status_code >= 400:
            raise PermanentCustomerApiError(
                f"customer API returned HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise RetryableCustomerApiError("customer API returned non-JSON response") from exc

    @staticmethod
    def _extract(payload: Any, path: str) -> Any:
        current = payload
        for segment in (part for part in path.split(".") if part):
            if not isinstance(current, dict) or segment not in current:
                raise PermanentCustomerApiError(f"response path not found: {path}")
            current = current[segment]
        return current

    @staticmethod
    def _customer_api_headers() -> dict[str, str]:
        return {"channelSystem": "AB", "traceId": str(uuid4())}

    @staticmethod
    def _format_data_date(value: Any) -> str:
        if isinstance(value, datetime):
            return value.strftime("%Y%m%d")
        if isinstance(value, date):
            return value.strftime("%Y%m%d")
        if isinstance(value, str):
            compact = value.replace("-", "")
            if len(compact) == 8 and compact.isdigit():
                return compact
        raise PermanentCustomerApiError("dataDate must be a date in YYYY-MM-DD or YYYYMMDD format")


def build_customer_provider(agent_config: dict[str, Any]) -> CustomerDataProvider:
    config = CustomerProviderConfig.model_validate(agent_config.get("customer_provider", {}))
    return HttpCustomerDataProvider(config)
