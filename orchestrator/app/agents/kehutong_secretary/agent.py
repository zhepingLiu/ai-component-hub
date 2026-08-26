from typing import Any

from ...core.context import AgentExecutionContext
from ..batch_agent.agent import BatchAgent
from .schema import KehutongBatchPayload, KehutongSecretaryReq


class KehutongSecretaryAgent(BatchAgent):
    name = "kehutong-secretary"
    log_event_prefix = "kehutong_secretary"
    batch_type = "kehutong_secretary"

    async def validate_request(self, payload: dict[str, Any]) -> KehutongSecretaryReq:
        return KehutongSecretaryReq.model_validate(payload)

    async def build_batch_payload(
        self,
        ctx: AgentExecutionContext,
        req: KehutongSecretaryReq,
    ) -> dict[str, Any]:
        return KehutongBatchPayload(inputs=req.inputs, options=req.options).model_dump(mode="json")
