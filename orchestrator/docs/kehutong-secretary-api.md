# 客户通秘书（kehutong-secretary）API 说明

## 1. 提交客户通秘书任务

### 1.1 接口信息

- 方法：`POST`
- 对外地址：`/api/agents/kehutong-secretary`
- Content-Type：`application/json`

该接口创建一个异步、可持久化的客户批处理任务。系统先查询活跃客户列表，再查询每个客户的好友关系。全部关系查询成功后，按客户列表及员工关系顺序，将每 `options.groups_per_request` 个 `(customer_id, emp_user_id)` 组合创建为一个子任务，读取各组合的聊天记录及客户跟进计划，一次调用目标 Agent。同一客户的员工优先填充当前请求，不足时用下一个客户补齐；同一客户可以跨请求拆分，最后不足容量时按实际组数发送。默认值为 `1`，保持原来的单组行为。子任务按客户列表及关系响应的顺序串行执行，前一任务重试期间后续任务等待；前一任务成功或最终失败后继续下一任务。

### 1.2 请求参数

所有业务字段都有默认值，因此 JSON 对象本身可以为空；生产调用建议显式传入 `request_id` 和业务日期。

| 参数路径                                      | 类型                    | 必填 | 默认值             | 约束/说明                                                                                                          |
| --------------------------------------------- | ----------------------- | ---: | ------------------ | ------------------------------------------------------------------------------------------------------------------ |
| `request_id`                                  | string                  |   否 | 系统生成 UUID      | 业务请求唯一标识，也是幂等键的一部分。相同 Agent 下重复提交相同 `request_id`，系统返回已有任务状态，不重复创建批次 |
| `inputs`                                      | object                  |   否 | `{}`               | 业务输入                                                                                                           |
| `inputs.customer_list_request`                | object                  |   否 | `{}`               | 客户列表查询参数                                                                                                   |
| `inputs.customer_list_request.last_id`   | integer                 |   否 | `0`                | 本次查询游标，非负整数；由 client 传入并负责后续分页                                                                                |
| `inputs.customer_list_request.pageSize`       | integer                 |   否 | `50`               | 本次客户列表查询条数，取值范围 `1~2000`；服务端仅查询一次                                        |
| `inputs.customer_data_request`                | object                  |   否 | `{}`               | 客户数据查询参数                                                                                                   |
| `inputs.customer_data_request.dataDate`       | string(date)            |   否 | 上海时区当天日期   | 业务日期，请求格式为 `YYYY-MM-DD`；调用聊天接口时转换为 `YYYYMMDD`                                                 |
| `options`                                     | object                  |   否 | `{}`               | 执行选项                                                                                                           |
| `options.scheduled_at`                        | string(datetime) / null |   否 | `null`             | 定时执行时间，必须包含时区偏移，例如 `2026-09-18T20:00:00+08:00`                                                   |
| `options.groups_per_request` | integer | 否 | `1` | 单次目标 Agent 请求最多包含的客户—员工组合数，必须为正整数；与批次及调用并发数无关 |
| `options.max_customers`                       | integer                 |   否 | `10000`            | 单批次最大客户数，取值范围 `1~100000`；查询结果超过该值时批次失败                                                  |
| `options.deduplicate_customer_ids`            | boolean                 |   否 | `true`             | 是否按首次出现顺序去重客户号                                                                                       |
| `options.fail_on_any_task_error`              | boolean                 |   否 | `false`            | 为 `true` 时，只要有客户子任务失败，最终 Agent 状态即为 `FAILED`；为 `false` 时允许部分失败并返回结果汇总          |
| `options.agent_options`                       | object                  |   否 | `{}`               | 原样传给目标 Agent 的扩展选项                                                                                      |
| `options.batch`                               | object                  |   否 | `{}`               | 批处理、超时和重试配置                                                                                             |
| `options.batch.max_concurrency`               | integer                 |   否 | `10`               | 并发上限，范围 `1~100`；本业务通过任务依赖保持串行                                                                                   |
| `options.batch.max_attempts`                  | integer                 |   否 | `3`                | 单个客户任务最大尝试次数，范围 `1~10`                                                                              |
| `options.batch.task_timeout_seconds`          | integer                 |   否 | `300`              | 单个客户任务超时秒数，范围 `10~86400`                                                                              |
| `options.batch.retry_delays_seconds`          | integer[]               |   否 | `[60,300,1200]`    | 客户任务重试等待秒数；每项范围 `0~86400`                                                                           |
| `options.batch.prepare_max_attempts`          | integer                 |   否 | `3`                | 准备阶段最大尝试次数，范围 `1~10`                                                                                  |
| `options.batch.prepare_retry_delays_seconds`  | integer[]               |   否 | `[30,120,600]`     | 准备阶段重试等待秒数；每项范围 `0~86400`                                                                           |
| `options.batch.finalize_max_attempts`         | integer                 |   否 | `5`                | 结果汇总/发布阶段最大尝试次数，范围 `1~10`                                                                         |
| `options.batch.finalize_retry_delays_seconds` | integer[]               |   否 | `[30,120,300,900]` | 结果发布重试等待秒数；每项范围 `0~86400`                                                                           |

### 1.3 请求示例

```bash
curl -X POST '{gateway_base_url}/api/agents/kehutong-secretary' \
  -H 'Content-Type: application/json' \
  -H 'X-Api-Key: replace-me' \
  -H 'X-Trace-Id: trace-20260918-001' \
  -d '{
    "request_id": "kht-20260918-001",
    "inputs": {
      "customer_list_request": {
        "last_id": 0,
        "pageSize": 50
      },
      "customer_data_request": {
        "dataDate": "2026-09-18"
      }
    },
    "options": {
      "groups_per_request": 3,
      "max_customers": 10000,
      "deduplicate_customer_ids": true,
      "fail_on_any_task_error": false,
      "batch": {
        "max_concurrency": 10,
        "max_attempts": 3,
        "task_timeout_seconds": 300,
        "retry_delays_seconds": [60, 300, 1200]
      },
      "agent_options": {}
    }
  }'
```

### 1.4 成功响应

Gateway 响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "request_id": "kht-20260918-001",
    "status": "QUEUED"
  }
}
```

任务从队列进入批次后，查询响应中的 `result` 会包含批次 ID：

```json
{
  "request_id": "kht-20260918-001",
  "status": "RUNNING",
  "result": {
    "batch_id": "<batch_id>",
    "batch_status": "CREATED"
  }
}
```

| 字段         | 类型          | 说明                                                                                      |
| ------------ | ------------- | ----------------------------------------------------------------------------------------- |
| `request_id` | string        | 业务请求 ID                                                                               |
| `status`     | string        | 当前 Agent 状态，例如 `QUEUED`、`RUNNING`、`SCHEDULED`、`SUCCEEDED`、`FAILED`、`REJECTED` |
| `result`     | object / null | 运行中包含 `batch_id`、`batch_status`；完成后包含汇总结果和结果文件信息                   |
| `error`      | string / null | 失败原因；无错误时省略                                                                    |

### 1.5 常见错误

| HTTP 状态 | 场景                                       | 响应说明                                                                                  |
| --------: | ------------------------------------------ | ----------------------------------------------------------------------------------------- |
|     `400` | 请求体不是合法 JSON                        | 编排器返回 `invalid_json`；经 Gateway 调用时通常包装为 HTTP `502`、`code=400`             |
|     `401` | Gateway 开启鉴权但 API Key 缺失或错误      | `unauthorized` / `invalid_api_key`                                                        |
|     `404` | Agent 未启用、未注册或路由不存在           | `component_not_found` 或 `agent_not_found`                                                |
|     `422` | 字段类型、范围或时区校验失败               | Pydantic 参数校验错误                                                                     |
|     `429` | Agent 内存队列已满                         | `status=REJECTED`，`error=queue_full`；经 Gateway 调用时通常包装为 HTTP `502`、`code=429` |
|     `502` | Gateway 无法连接编排器，或上游返回 4xx/5xx | `bad_gateway` 或 `upstream_error`                                                         |
|     `504` | Gateway 请求上游超时                       | `upstream_timeout`                                                                        |

## 2. 查询客户通秘书任务状态

### 2.1 接口信息

- 方法：`GET`
- 对外地址：`/api/agents/kehutong-secretary`
- 编排器直连地址：`/agents/kehutong_secretary`

### 2.2 Query 参数

| 参数         | 类型   |         必填 | 说明                                                                                                             |
| ------------ | ------ | -----------: | ---------------------------------------------------------------------------------------------------------------- |
| `request_id` | string | 是（业务上） | 提交任务时使用或返回的业务请求 ID。代码允许省略，但省略时会生成新 ID，通常只能得到 `UNKNOWN`，因此查询时应当必传 |

### 2.3 请求示例

```bash
curl -G '{gateway_base_url}/api/agents/kehutong-secretary' \
  -H 'X-Api-Key: replace-me' \
  --data-urlencode 'request_id=kht-20260918-001'
```

### 2.4 运行中响应示例

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "request_id": "kht-20260918-001",
    "status": "RUNNING",
    "result": {
      "batch_id": "<batch_id>",
      "batch_status": "CREATED"
    },
    "error": null
  }
}
```

GET `/api/agents/kehutong-secretary?request_id=...` 的完成响应在 `data.result.last_id` 中返回下一页游标。该值为本页客户记录的最大整数 `id`（不会小于本次传入的 `last_id`），不是 `cust_no`，也不受客户去重、员工关系数量或任务分组影响。空页沿用本次传入的游标。下一次 POST 时，将其传入 `inputs.customer_list_request.last_id`，并使用新的 `request_id`。

游标在准备阶段成功后持久化，完成通知时写入 GET 结果；准备尚未完成时可能没有此字段。准备完成后，即使汇总或交付失败，终态结果仍保留游标，但客户端应先处理失败任务再决定是否翻页。客户列表记录缺少有效整数 `id` 时会报错，避免返回错误游标。历史批次未保存游标时不会补造该字段。

### 2.5 完成响应示例

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "request_id": "kht-20260918-001",
    "status": "SUCCEEDED",
    "result": {
      "summary": {
        "total": 2,
        "succeeded": 2,
        "failed": 0,
        "cancelled": 0
      },
      "result_url": "http://file-server/results/SSC/kht-20260918-001-result.json",
      "last_id": 1000000059,
      "server_file": "kht-20260918-001-result.json",
      "size_bytes": 1024,
      "sha256": "<sha256>",
      "batch_id": "<batch_id>",
      "callback": {
        "status": "SKIPPED",
        "error": null,
        "attempts": 0
      }
    },
    "error": null
  }
}
```

> `callback.status` 可能为 `OK`、`FAILED` 或 `SKIPPED`；未配置 `callback_url` 时为 `SKIPPED`。最终客户明细不会直接放进查询响应，而是写入 `result_url` 指向的 JSON 文件。

## 3. 批次查询接口

提交任务并获得 `batch_id` 后，可以查询批次及客户子任务。对外路径通过已注册的 `batches/run` 路由访问；编排器直连路径以 `/batches` 开头。

### 3.1 查询批次详情

- 方法：`GET`
- 对外地址：`/api/batches/run/{batch_id}`
- 编排器直连地址：`/batches/{batch_id}`

#### Path 参数

| 参数       | 类型   | 必填 | 说明                    |
| ---------- | ------ | ---: | ----------------------- |
| `batch_id` | string |   是 | 提交任务后获得的批次 ID |

主要响应字段：

| 字段           | 类型          | 说明                                                                                                                                                             |
| -------------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `batch_id`     | string        | 批次 ID                                                                                                                                                          |
| `batch_type`   | string        | 客户通秘书固定为 `kehutong_secretary`                                                                                                                            |
| `status`       | string        | `SCHEDULED`、`CREATED`、`PREPARING`、`GENERATING_TASKS`、`RUNNING`、`PAUSED`、`CANCELLING`、`FINALIZING`、`SUCCEEDED`、`PARTIAL_FAILED`、`FAILED` 或 `CANCELLED` |
| `scheduled_at` | string / null | 计划执行时间                                                                                                                                                     |
| `total`        | integer       | 子任务总数                                                                                                                                                       |
| `pending`      | integer       | 等待执行数                                                                                                                                                       |
| `running`      | integer       | 执行中数量                                                                                                                                                       |
| `succeeded`    | integer       | 成功数量                                                                                                                                                         |
| `failed`       | integer       | 失败数量                                                                                                                                                         |
| `cancelled`    | integer       | 取消数量                                                                                                                                                         |
| `result`       | object / null | 批次最终结果                                                                                                                                                     |
| `error`        | string / null | 批次错误信息                                                                                                                                                     |
| `created_at`   | string        | 创建时间                                                                                                                                                         |
| `started_at`   | string / null | 开始时间                                                                                                                                                         |
| `finished_at`  | string / null | 完成时间                                                                                                                                                         |

### 3.2 查询客户子任务列表

- 方法：`GET`
- 对外地址：`/api/batches/run/{batch_id}/tasks`
- 编排器直连地址：`/batches/{batch_id}/tasks`

#### 参数

| 参数       | 位置  | 类型    | 必填 | 默认值 | 说明                         |
| ---------- | ----- | ------- | ---: | ------ | ---------------------------- |
| `batch_id` | Path  | string  |   是 | -      | 批次 ID                      |
| `cursor`   | Query | integer |   否 | `0`    | 分页偏移量，必须大于等于 `0` |
| `limit`    | Query | integer |   否 | `50`   | 每页条数，范围 `1~200`       |

响应结构：

```json
{
  "items": [
    {
      "task_id": "<task_id>",
      "batch_id": "<batch_id>",
      "business_key": "[\"1001\",\"manager-1\"]",
      "status": "SUCCEEDED",
      "payload": {"customer_id": "1001", "emp_user_id": "manager-1"},
      "attempt": 1,
      "max_attempts": 3,
      "output": {
        "customer_id": "1001",
        "emp_user_id": "manager-1",
        "agent_request_id": "<batch_id>:[\"1001\",\"manager-1\"]",
        "result": {}
      },
      "error_code": null,
      "error": null,
      "created_at": "<datetime>",
      "started_at": "<datetime>",
      "finished_at": "<datetime>"
    }
  ],
  "next_cursor": null
}
```

### 3.3 查询单个客户子任务

- 方法：`GET`
- 对外地址：`/api/batches/run/{batch_id}/tasks/{task_id}`
- 编排器直连地址：`/batches/{batch_id}/tasks/{task_id}`

| 参数       | 位置 | 类型   | 必填 | 说明          |
| ---------- | ---- | ------ | ---: | ------------- |
| `batch_id` | Path | string |   是 | 批次 ID       |
| `task_id`  | Path | string |   是 | 客户子任务 ID |

子任务状态包括：`PENDING`、`RUNNING`、`RETRY_WAIT`、`SUCCEEDED`、`FAILED`、`CANCELLED`。

## 4. 批次控制接口

以下接口均不需要请求体。

| 方法   | 对外地址                                   | 编排器直连地址                     | 说明                     |
| ------ | ------------------------------------------ | ---------------------------------- | ------------------------ |
| `POST` | `/api/batches/run/{batch_id}/pause`        | `/batches/{batch_id}/pause`        | 暂停批次                 |
| `POST` | `/api/batches/run/{batch_id}/resume`       | `/batches/{batch_id}/resume`       | 恢复暂停批次             |
| `POST` | `/api/batches/run/{batch_id}/cancel`       | `/batches/{batch_id}/cancel`       | 取消批次                 |
| `POST` | `/api/batches/run/{batch_id}/retry-failed` | `/batches/{batch_id}/retry-failed` | 重新执行失败的客户子任务 |

公共 Path 参数：

| 参数       | 类型   | 必填 | 说明              |
| ---------- | ------ | ---: | ----------------- |
| `batch_id` | string |   是 | 需要控制的批次 ID |

可能的状态码：批次不存在返回 `404`；当前状态不允许执行指定操作时返回 `409`。

### 4.1 重新安排执行时间

- 方法：`POST`
- 对外地址：`/api/batches/run/{batch_id}/reschedule`
- 编排器直连地址：`/batches/{batch_id}/reschedule`

请求体：

| 参数           | 类型             | 必填 | 说明                           |
| -------------- | ---------------- | ---: | ------------------------------ |
| `scheduled_at` | string(datetime) |   是 | 新的执行时间，必须包含时区偏移 |

```json
{
  "scheduled_at": "2026-09-19T09:00:00+08:00"
}
```

只有状态为 `SCHEDULED` 的批次可以重新安排时间，否则返回 `409`。

## 5 传给目标 Agent 的数据

客户聊天记录和跟进计划会聚合为：

```json
{
  "follow_plan": [],
  "msgs": {
    "rows": [],
    "totalNum": 0,
    "pageNum": 1,
    "pageSize": 2000
  }
}
```

默认传给目标 Agent 的字段如下；字段名可在 `agents.yaml` 中修改：

| 默认字段         | 类型         | 说明                                                                            |
| ---------------- | ------------ | ------------------------------------------------------------------------------- |
| `customerId`     | string       | 客户号；由 `agent_customer_id_field` 配置字段名                                 |
| `aggregateData`  | string(JSON) | 上述聚合对象序列化后的 JSON 字符串；由 `agent_customer_data_field` 配置字段名   |
| `batchRequestId` | string       | 格式为 `{batch_id}:{business_key}`，其中 business_key 为客户和员工 ID 的 JSON 数组字符串；由 `agent_batch_request_id_field` 配置字段名 |

### 5.1 多组请求与返回格式

当 `options.groups_per_request > 1` 时，`aggregateData` 为下面数组序列化后的 JSON 字符串。每项包含客户和员工标识，以及原有聚合对象。即使最后一次只有一组，也保持数组格式；不再发送顶层 `customerId`。`aggregateData` 和 `batchRequestId` 仍支持原有字段名配置。

```json
[
  {
    "customer_id": "customer-A",
    "emp_user_id": "employee-1",
    "aggregate_data": {"follow_plan": [], "msgs": {"rows": [], "totalNum": 0}}
  },
  {
    "customer_id": "customer-B",
    "emp_user_id": "employee-2",
    "aggregate_data": {"follow_plan": [], "msgs": {"rows": [], "totalNum": 0}}
  }
]
```

目标 Agent 返回数据中的 `answer` 必须为以下数组或该数组的 JSON 字符串：

```json
{
  "answer": [
    {"customer_id": "customer-A", "emp_user_id": "employee-1", "result": "分析结果 A"},
    {"customer_id": "customer-B", "emp_user_id": "employee-2", "result": "分析结果 B"}
  ]
}
```

`result` 可以为任意 JSON 值。系统按 `(customer_id, emp_user_id)` 匹配，允许返回顺序不同；缺失、重复或不属于当前请求的组合，以及无效 JSON，都会导致该请求失败并按配置重试。启用多组模式前，目标 Agent 需要支持此输入和输出格式。

多组请求的 `business_key` 是组合数组，例如 `[["customer-A","employee-1"],["customer-B","employee-2"]]`。`batchRequestId` 仍为 `{batch_id}:{business_key}`，重试保持不变。

每个请求是一个持久化子任务，也是超时、重试、取消和失败处理的单位。任一组合的数据读取失败时，该次请求不会调用目标 Agent；重试会重新读取整个请求的数据。请求之间仍串行执行，组内的并行分析由目标 Agent 完成。系统仅使用本次客户列表查询页中的客户补齐，不自动请求下一页。

批次接口和最终 `summary` 中的计数均为请求任务数；结果文件的 `customers` 则按组合展开，保留每组自己的结果。一个请求最终失败或取消时，其所有组合共享该任务状态、尝试次数和错误信息。

## 6. 完成回调(当前未配置)

配置 `callback_url` 后，整个批次结束时系统会以 `POST` 方式发送回调。未配置或配置为空字符串时不发送。

请求体：

```json
{
  "request_id": "kht-20260918-001",
  "trace_id": "trace-20260918-001",
  "agent": "kehutong_secretary",
  "status": "SUCCEEDED",
  "result": {
    "summary": {
      "total": 2,
      "succeeded": 2,
      "failed": 0,
      "cancelled": 0
    },
    "result_url": "http://file-server/results/SSC/kht-20260918-001-result.json",
    "server_file": "kht-20260918-001-result.json",
    "size_bytes": 1024,
    "sha256": "<sha256>"
  },
  "error": ""
}
```

| 参数         | 类型   | 必填 | 说明                                        |
| ------------ | ------ | ---: | ------------------------------------------- |
| `request_id` | string |   是 | 原业务请求 ID                               |
| `trace_id`   | string |   是 | 原追踪 ID；没有时为空字符串                 |
| `agent`      | string |   是 | Agent 配置名称，通常为 `kehutong_secretary` |
| `status`     | string |   是 | `SUCCEEDED` 或 `FAILED`                     |
| `result`     | object |   是 | 汇总结果；无结果时为空对象                  |
| `error`      | string |   是 | 错误信息；无错误时为空字符串                |

## 7. 结果文件格式

最终明细文件为 JSON，文件名格式为 `{request_id}-result.json`。不安全的文件名字符会被替换为下划线。上传地址仍使用 `result_server_path`（默认 `ESB_BASE_URL`），上传文件名不添加子目录。返回的下载地址 `result_url` 默认使用 `ESB_BASE_URL/SSC/{文件名}`；可通过 `result_public_base_url` 指定完整下载前缀（包含所需的 `/SSC`），显式配置时不会额外追加 `/SSC`。旧配置项 `result_subdir` 不再生效。

```json
{
  "request_id": "kht-20260918-001",
  "batch_id": "<batch_id>",
  "agent": "kehutong_secretary",
  "generated_at": "2026-09-18T12:00:00+00:00",
  "summary": {
    "total": 2,
    "succeeded": 1,
    "failed": 1,
    "cancelled": 0
  },
  "customers": [
    {
      "customer_id": "1001",
      "emp_user_id": "manager-1",
      "status": "SUCCEEDED",
      "attempts": 1,
      "result": {}
    },
    {
      "customer_id": "1001",
      "emp_user_id": "manager-2",
      "status": "FAILED",
      "attempts": 3,
      "error": "downstream unavailable"
    }
  ]
}
```

## 8. 实现注意事项

1. 客户列表接口仅按 client 传入的 `last_id` 和 `pageSize` 查询一次，响应 `data` 为 `[{"cust_no":"客户号","id":1}, ...]`。服务端只处理本次响应中的客户，不提取下一页游标，也不循环查询；后续分页由 client 负责。后续业务使用字符串 `cust_no`，不依赖响应中的分页 `id`。
2. 查询聊天记录和跟进计划会并发执行；任一接口失败都会使当前客户与客户经理组合的子任务进入失败或重试流程。
3. HTTP `408`、`429`、`500`、`502`、`503`、`504` 默认视为可重试错误；其他 `4xx/5xx` 默认视为永久错误。该集合可通过 `retryable_status_codes` 调整。
4. 客户依赖接口返回非 JSON 时视为可重试错误；响应结构不符合本文约定时视为永久数据错误。

## 9. 好友关系接口与部署配置

`customer_provider.customer_relations_api` 为新增必填配置，配置示例见 `orchestrator/agents.yaml`。

- 方法：`POST`
- UAT：`http://10.197.129.35:6603/api/kht/sjfw_mysql/queryCustomerRelate`
- 请求头：`channelSystem: AB`，每次请求生成独立 `traceId`。
- 请求体：`{"customer_id":"a"}`。
- 响应示例：

```json
{
  "data": [
    {"customer_id": "a", "emp_user_id": "1"},
    {"customer_id": "a", "emp_user_id": "2"}
  ],
  "stateCode": "0000",
  "message": "服务调用成功"
}
```

相同关系按首次出现顺序去重。成功响应的 `data: []` 表示无关系，不创建子任务。
非成功响应码、缺失响应码或无效关系数据会使准备阶段失败并按准备阶段策略重试，不会作为空关系跳过；耗尽重试后批次失败。

每个子任务调用聊天接口时使用 `to_list=customer_id`、`from1=emp_user_id`，日期和分页方式不变。跟进计划仍按 `target_id=customer_id` 查询，下游 Agent 的输入字段和聚合数据结构保持不变。

结果文件仍使用 `customers` 数组，每条增加 `emp_user_id`。汇总中的 `total`、`succeeded`、`failed`、`cancelled` 统计的是关系子任务数，而非客户数。手动重试失败子任务时也按原顺序串行执行。

上线前需在实际启用的配置中增加关系接口，并等待旧版在途批次结束后再切换；旧版已创建的客户粒度任务不自动迁移为关系粒度任务。
