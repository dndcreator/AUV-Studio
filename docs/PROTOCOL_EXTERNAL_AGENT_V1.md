# 外接 Agent 文本协议 v1.0

最后更新：2026-09-30

## 1. 目标

允许用户将自有 Agent 服务接入 AUV 协作框架，AUV 只依赖标准化输入输出，不依赖外部 Agent 内部实现。

## 2. 调用方式

- 方法：`POST`
- 路径：默认 `/agent/tasks`
- 模式：同步 `reply_mode=sync`
- 前端节点类型：`external_agent`
- 接入方式：
  - `http`：你的 agent 已经有本地 HTTP API，直接填写 `endpoint_url`
  - `bridge_local`：你的 agent 没有标准 API，先写一个本地 adapter/bridge，默认监听 `http://127.0.0.1:8787`

`endpoint_url` 只接受 `http/https`。AUV 会校验 HTTP 状态、响应结构和 `task_id`；响应中的 `task_id` 必须原样回传请求值。

> AUV 不要求你的 agent 内部使用任何特定框架。它只要求外部服务接收一份标准任务 JSON，并返回一份标准结果 JSON。

## 3. 请求字段（核心）

- `protocol_version`
- `task_id`
- `run_id`
- `node_id`
- `context.environment`
- `context.global_guidance`
- `context.interactions`
- `input.text`（增强后的 prompt）
- `input.raw_prompt`
- `input.background.environment`

`environment.context_book` is removed before an external request is built. Context Book entries can contain node-private knowledge and are not sent to external agents in protocol v1.0.
- `input.background.global_guidance`
- `input.background.interactions`
- `input.background.memory`
- `constraints.max_tokens`
- `constraints.timeout_ms`

## 4. 响应字段（核心）

- `task_id`
- `status`（succeeded/failed）
- `output.text`
- `metrics.latency_ms`
- `metrics.token_in`
- `metrics.token_out`
- `errors`

最小成功响应：

```json
{
  "task_id": "task_xxx",
  "status": "succeeded",
  "output": {
    "text": "这里是你的 agent 返回给 AUV 的协作输出"
  },
  "metrics": {
    "latency_ms": 1200,
    "token_in": 800,
    "token_out": 300
  },
  "errors": []
}
```

最小失败响应：

```json
{
  "task_id": "task_xxx",
  "status": "failed",
  "output": {},
  "metrics": {},
  "errors": [
    {"code": "AGENT_ERROR", "message": "具体错误原因"}
  ]
}
```

## 5. 兼容建议

1. 未用字段原样透传，不中断主流程
2. 返回失败时填充 errors 便于平台追踪
3. 对 prompt 注入保持幂等，避免重复拼接
4. 响应体建议带业务 trace_id 便于跨系统排障
5. HTTP 错误响应应包含简短可读信息，AUV 会截取并写入节点错误记录

## 6. 用户是否必须改造自己的 agent？

分两种情况：

1. agent 已经有 API：只需要让它的 API 接收/返回本协议，或者在现有 API 前面加一个很薄的转换层。
2. agent 没有 API：需要写一个 adapter，把 AUV 的 `input.text/context` 转成该 agent 能理解的输入，再把 agent 输出转成 `output.text`。

这是协议级通用方案的边界：AUV 可以统一协作框架和上下文格式，但无法自动调用一个完全没有输入输出接口的 agent。
