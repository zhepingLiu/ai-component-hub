from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ...batch.definition import BatchContext, BatchTaskSpec
from ...batch.models import TaskRecord
from ...batch.results import TaskExecutionResult
from ..batch_agent.definition import BatchAgentDefinition
from .client import (
    PermanentCustomerApiError,
    RetryableCustomerApiError,
    build_customer_provider,
)
from .result_delivery import publish_kehutong_result
from .schema import KehutongBatchPayload


class KehutongSecretaryBatchDefinition(BatchAgentDefinition):
    batch_type = "kehutong_secretary"
    version = "1"

    async def validate_business_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return KehutongBatchPayload.model_validate(payload).model_dump(mode="json")

    async def prepare(self, ctx: BatchContext, batch_input: dict[str, Any]) -> dict[str, Any]:
        envelope = self.envelope(ctx)
        payload = KehutongBatchPayload.model_validate(envelope.payload)
        provider = build_customer_provider(self.agent_config(ctx))
        customer_ids = await provider.list_customer_ids(payload.inputs.customer_list_request)
        if payload.options.deduplicate_customer_ids:
            customer_ids = list(dict.fromkeys(customer_ids))
        if len(customer_ids) > payload.options.max_customers:
            raise RuntimeError(
                f"customer count {len(customer_ids)} exceeds configured limit "
                f"{payload.options.max_customers}"
            )
        return {
            "customer_ids": customer_ids,
            "customer_data_request": payload.inputs.customer_data_request,
            "agent_inputs": payload.inputs.agent_inputs,
            "agent_options": payload.options.agent_options,
        }

    async def produce_tasks(
        self,
        ctx: BatchContext,
        batch_input: dict[str, Any],
        runtime_context: dict[str, Any],
    ) -> AsyncIterator[BatchTaskSpec]:
        for customer_id in runtime_context.get("customer_ids", []):
            yield BatchTaskSpec(
                business_key=str(customer_id),
                payload={"customer_id": str(customer_id)},
            )

    async def execute_task(
        self,
        ctx: BatchContext,
        task: TaskRecord,
        runtime_context: dict[str, Any],
    ) -> TaskExecutionResult:
        customer_id = str(task.payload["customer_id"])
        config = self.agent_config(ctx)
        try:
            customer_data = await build_customer_provider(config).get_customer_data(
                customer_id,
                dict(runtime_context.get("customer_data_request") or {}),
            )
        except PermanentCustomerApiError as exc:
            return TaskExecutionResult.permanent_failure("CUSTOMER_DATA_INVALID", str(exc))
        except RetryableCustomerApiError as exc:
            return TaskExecutionResult.retryable_failure("CUSTOMER_DATA_UNAVAILABLE", str(exc))
        except Exception as exc:
            return TaskExecutionResult.retryable_failure("CUSTOMER_DATA_ERROR", str(exc))

        agent_inputs = dict(runtime_context.get("agent_inputs") or {})
        customer_id_field = str(config.get("agent_customer_id_field", "customerId"))
        customer_data_field = str(config.get("agent_customer_data_field", "customerData"))
        request_id_field = str(config.get("agent_batch_request_id_field", "batchRequestId"))
        agent_inputs[customer_id_field] = customer_id
        agent_inputs[customer_data_field] = customer_data
        agent_inputs[request_id_field] = f"{ctx.batch.batch_id}:{customer_id}"

        target_config = config.get("target_agent", {}) or {}
        if not isinstance(target_config, dict):
            return TaskExecutionResult.permanent_failure(
                "TARGET_AGENT_CONFIG_INVALID",
                "target_agent config must be an object",
            )
        try:
            from ..generic_agent.workflow import build_generic_agent_client

            agent_result = await build_generic_agent_client(target_config).run(
                local_file_paths=[],
                inputs=agent_inputs,
                options=dict(runtime_context.get("agent_options") or {}),
            )
        except Exception as exc:
            return TaskExecutionResult.retryable_failure("TARGET_AGENT_ERROR", str(exc))
        if not agent_result.ok:
            return TaskExecutionResult.retryable_failure(
                "TARGET_AGENT_FAILED",
                agent_result.error or "target agent failed",
            )
        return TaskExecutionResult.succeeded(
            {
                "customer_id": customer_id,
                "agent_request_id": f"{ctx.batch.batch_id}:{customer_id}",
                "result": agent_result.data,
            }
        )

    async def finalize(self, ctx: BatchContext) -> dict[str, Any]:
        envelope = self.envelope(ctx)
        return await publish_kehutong_result(
            ctx=ctx,
            request_id=envelope.request_id,
            agent_name=envelope.agent_name,
            agent_config=self.agent_config(ctx),
        )
