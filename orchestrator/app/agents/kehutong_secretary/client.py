from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .schema import CustomerProviderConfig, HttpApiConfig


class RetryableCustomerApiError(RuntimeError):
    pass


class PermanentCustomerApiError(RuntimeError):
    pass


class CustomerDataProvider(Protocol):
    async def list_customer_ids(self, request: dict[str, Any]) -> list[str]: ...

    async def get_customer_data(self, customer_id: str, request: dict[str, Any]) -> Any: ...


class HttpCustomerDataProvider:
    def __init__(self, config: CustomerProviderConfig):
        self.config = config

    async def list_customer_ids(self, request: dict[str, Any]) -> list[str]:
        api = self.config.customer_list_api
        payload = await self._request(api, request)
        path = api.response.customer_ids_path or "customerIds"
        customer_ids = self._extract(payload, path)
        if not isinstance(customer_ids, list):
            raise PermanentCustomerApiError(
                f"customer id response path '{path}' did not resolve to a list"
            )
        return [str(value) for value in customer_ids if value is not None]

    async def get_customer_data(self, customer_id: str, request: dict[str, Any]) -> Any:
        api = self.config.customer_data_api
        payload = await self._request(api, request, customer_id=customer_id)
        path = api.response.customer_data_path
        return self._extract(payload, path) if path else payload

    async def _request(
        self,
        api: HttpApiConfig,
        request: dict[str, Any],
        *,
        customer_id: str | None = None,
    ) -> Any:
        url = api.url
        body = {**api.static_body, **request}
        query: dict[str, Any] = {}
        if customer_id is not None:
            binding = api.customer_id
            if binding is None:
                raise PermanentCustomerApiError("customer_data_api.customer_id is required")
            if binding.location == "body":
                body[binding.field] = customer_id
            elif binding.location == "query":
                query[binding.field] = customer_id
            else:
                placeholder = "{" + binding.field + "}"
                if placeholder not in url:
                    raise PermanentCustomerApiError(
                        f"customer id placeholder not found in URL: {placeholder}"
                    )
                url = url.replace(placeholder, quote(customer_id, safe=""))

        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                if api.method == "GET":
                    response = await client.get(
                        url,
                        params={**body, **query},
                        headers=self.config.headers,
                    )
                else:
                    response = await client.post(
                        url,
                        params=query or None,
                        json=body,
                        headers=self.config.headers,
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


def build_customer_provider(agent_config: dict[str, Any]) -> CustomerDataProvider:
    config = CustomerProviderConfig.model_validate(agent_config.get("customer_provider", {}))
    return HttpCustomerDataProvider(config)
