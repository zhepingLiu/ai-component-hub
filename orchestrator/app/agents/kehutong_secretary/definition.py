from __future__ import annotations

import json
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
        page = await provider.list_customer_page(
            payload.inputs.customer_list_request.model_dump(mode="json")
        )
        customer_ids = page.customer_ids
        if payload.options.deduplicate_customer_ids:
            customer_ids = list(dict.fromkeys(customer_ids))
        if len(customer_ids) > payload.options.max_customers:
            raise RuntimeError(
                f"customer count {len(customer_ids)} exceeds configured limit "
                f"{payload.options.max_customers}"
            )
        relations = []
        for customer_id in customer_ids:
            for emp_user_id in await provider.list_employee_ids(customer_id):
                relations.append({"customer_id": customer_id, "emp_user_id": emp_user_id})
        return {
            "last_id": page.last_id,
            "customer_relations": relations,
            "groups_per_request": payload.options.groups_per_request,
            "customer_data_request": payload.inputs.customer_data_request.model_dump(mode="json"),
            "agent_inputs": self.build_agent_inputs(),
            "agent_options": payload.options.agent_options,
        }

    def build_agent_inputs(self) -> dict[str, Any]:
        """Kehutong-specific base inputs merged with each customer's data."""
        return {}

    async def produce_tasks(
        self,
        ctx: BatchContext,
        batch_input: dict[str, Any],
        runtime_context: dict[str, Any],
    ) -> AsyncIterator[BatchTaskSpec]:
        relations = runtime_context["customer_relations"]
        size = runtime_context.get("groups_per_request", 1)
        for offset in range(0, len(relations), size):
            group = relations[offset:offset + size]
            pairs = [[r["customer_id"], r["emp_user_id"]] for r in group]
            yield BatchTaskSpec(
                business_key=json.dumps(
                    pairs[0] if size == 1 else pairs,
                    ensure_ascii=False, separators=(",", ":"),
                ),
                payload=dict(group[0]) if size == 1 else {"groups": group},
                wait_for_previous=True,
            )

    async def execute_task(
        self,
        ctx: BatchContext,
        task: TaskRecord,
        runtime_context: dict[str, Any],
    ) -> TaskExecutionResult:
        grouped = "groups" in task.payload
        relations = task.payload["groups"] if grouped else [task.payload]
        agent_request_id = f"{ctx.batch.batch_id}:{task.business_key}"
        config = self.agent_config(ctx)
        try:
            provider = build_customer_provider(config)
            groups = []
            for relation in relations:
                customer_id = str(relation["customer_id"])
                emp_user_id = str(relation["emp_user_id"])
                aggregate_data = await provider.get_aggregate_data(
                    customer_id,
                    emp_user_id,
                    dict(runtime_context.get("customer_data_request") or {}),
                )
                groups.append({
                    "customer_id": customer_id,
                    "emp_user_id": emp_user_id,
                    "aggregate_data": aggregate_data,
                })
        except PermanentCustomerApiError as exc:
            return TaskExecutionResult.permanent_failure("CUSTOMER_DATA_INVALID", str(exc))
        except RetryableCustomerApiError as exc:
            return TaskExecutionResult.retryable_failure("CUSTOMER_DATA_UNAVAILABLE", str(exc))
        except Exception as exc:
            return TaskExecutionResult.retryable_failure("CUSTOMER_DATA_ERROR", str(exc))

        agent_inputs = dict(runtime_context.get("agent_inputs") or {})
        customer_id_field = str(config.get("agent_customer_id_field", "customerId"))
        customer_data_field = str(config.get("agent_customer_data_field", "aggregateData"))
        request_id_field = str(config.get("agent_batch_request_id_field", "batchRequestId"))
        # Multi-group requests identify customers inside each group, never by
        # a misleading single top-level customer ID.
        if grouped:
            agent_inputs.pop(customer_id_field, None)
        else:
            agent_inputs[customer_id_field] = customer_id
        agent_inputs[customer_data_field] = json.dumps(
            groups if grouped else aggregate_data,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        agent_inputs[request_id_field] = agent_request_id

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
        if grouped:
            try:
                results = self.parse_group_results(agent_result.data, relations)
            except (ValueError, TypeError, KeyError) as exc:
                return TaskExecutionResult.retryable_failure("TARGET_AGENT_RESULT_INVALID", str(exc))
            return TaskExecutionResult.succeeded({
                "groups": results,
                "agent_request_id": agent_request_id,
            })
        return TaskExecutionResult.succeeded(
            {
                "customer_id": customer_id,
                "emp_user_id": emp_user_id,
                "agent_request_id": agent_request_id,
                "result": agent_result.data,
            }
        )

    @staticmethod
    def parse_group_results(data: Any, relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Match explicit identities; response order is not a reliable identifier."""
        answer = data.get("answer") if isinstance(data, dict) else None
        if isinstance(answer, str):
            answer = json.loads(answer)
        if not isinstance(answer, list):
            raise ValueError("multi-group answer must be an array of customer_id, emp_user_id, result objects")
        expected = {(r["customer_id"], r["emp_user_id"]) for r in relations}
        indexed = {}
        for item in answer:
            if not isinstance(item, dict) or "result" not in item:
                raise ValueError("each group answer must contain customer_id, emp_user_id and result")
            key = (item["customer_id"], item["emp_user_id"])
            if key not in expected or key in indexed:
                raise ValueError("group answer contains an unexpected or duplicate customer/employee pair")
            indexed[key] = {
                "customer_id": key[0], "emp_user_id": key[1],
                "result": {"answer": item["result"]},
            }
        if indexed.keys() != expected:
            raise ValueError("group answer is missing customer/employee pairs")
        return [indexed[(r["customer_id"], r["emp_user_id"])] for r in relations]

    async def finalize(self, ctx: BatchContext) -> dict[str, Any]:
        envelope = self.envelope(ctx)
        return await publish_kehutong_result(
            ctx=ctx,
            request_id=envelope.request_id,
            agent_name=envelope.agent_name,
            agent_config=self.agent_config(ctx),
        )

    async def _complete_agent_request(
        self,
        ctx: BatchContext,
        *,
        status: str,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        # Preserve pagination metadata even when finalization/delivery fails.
        delivered = dict(result or {})
        if "last_id" in ctx.batch.runtime_context:
            delivered["last_id"] = ctx.batch.runtime_context["last_id"]
        await super()._complete_agent_request(
            ctx, status=status, result=delivered, error=error,
        )
