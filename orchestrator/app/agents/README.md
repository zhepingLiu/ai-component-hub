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

## Durable batch agents

Agents that fan one request out into durable tasks should inherit
`agents.batch_agent.BatchAgent` instead of implementing an in-process queue.
`BatchAgent` persists the batch and links it to the original agent request;
`app.batch` owns scheduling, leases, retry, pause/resume, and crash recovery.

Keep the business definition in the business agent package:

```text
agents/customer_analysis/
  agent.py          # CustomerAnalysisAgent(BatchAgent)
  definition.py     # prepare/produce_tasks/execute_task/finalize
  client.py         # business API provider implementations
  result_delivery.py
  handler.py
  schema.py
```

The definition must use stable `business_key` values and downstream idempotency
keys. Task execution is at-least-once; stable keys make retries effectively
idempotent when the downstream system honors them.
