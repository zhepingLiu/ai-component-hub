# Agent Development

Each agent owns its business code under `orchestrator/app/agents/<agent_name>/`.
Shared scheduling, idempotency, queueing, and job status updates live outside the
agent package.

Minimum structure:

```text
agents/contract_review/
  agent.py
  handler.py
  schema.py
  client.py
```

Implement `BaseAgent` in `agent.py`:

```python
from app.core import AgentExecutionContext, AgentExecutionResult, BaseAgent


class ContractReviewAgent(BaseAgent):
    name = "contract-review"
    log_event_prefix = "contract_review"

    async def validate_request(self, payload):
        return ContractReviewReq.model_validate(payload)

    async def execute(self, ctx: AgentExecutionContext, req) -> AgentExecutionResult:
        ...

    async def postprocess(self, ctx: AgentExecutionContext, req, result: AgentExecutionResult) -> AgentExecutionResult:
        return result
```

Keep `handler.py` thin. It should connect the agent to `AgentScheduler`, then
format the API response. Agent-specific workflow, callback payloads, and result
delivery should stay inside the agent package.
