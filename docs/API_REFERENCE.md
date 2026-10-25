# API 参考

最后更新：2026-10-25

基地址：`http://localhost:8000/api`

浏览器请求的 `Origin` 必须匹配 `AUV_ALLOWED_ORIGINS`。默认允许 localhost、127.0.0.1、[::1] 的 HTTP 5173/4173/8000 端口，不允许通配符；未授权来源返回 403。未携带 Origin 的本地客户端和外接 Agent 不受此限制。这不是身份认证，不应直接将服务暴露到公网。

## 研究证据包

### `POST /evidence/parse`

在本地解析用户提供的单个文件或 ZIP 证据包。请求体为原始二进制，`Content-Type: application/octet-stream`，文件名通过 `X-Filename` 请求头传递。

当前支持 `.zip/.xlsx/.docx/.csv/.json/.txt/.md`。ZIP 内文件使用同一白名单；`.pdf/.xls/.doc` 等格式会出现在 `skipped` 中，不会静默忽略。接口限制上传 25 MB、最多 100 个归档文件、解压后最多 80 MB，并拒绝路径穿越条目。

请求按流累计大小，声明或实际上传超过 25 MB 返回 413，包含无 Content-Length 的分块上传。无效 Content-Length 返回 400。Office 文档内部也限制 100 个归档条目和 80 MB 展开大小；单个文档失败记录在 `errors` 中。本地解析在线程池完成，不占用异步请求循环。

响应包含：

- `items`：已解析的文档、表格或结构化数据。
- `skipped`：不支持或无可读内容的文件。
- `errors`：损坏、加密或不安全文件。
- `summary`：成功、跳过、失败及字符数统计。

研究模式将确认后的响应作为 `PlanCompileRequest.evidence_pack` 重新提交到 `/templates/compile`。证据随后进入工作流 `environment.simulation.evidence`，供 Director、DAG 节点和持续模拟 Context 只读使用。

## 1. 工作流

- `POST /workflows` 创建工作流
- `GET /workflows` 工作流列表
- `GET /workflows/{id}` 获取工作流
- `PUT /workflows/{id}` 保存工作流：存在则更新，不存在则创建（upsert）

## 2. 执行与重试

- `POST /workflows/{id}/run` 启动运行
- `POST /runs/{run_id}/retry` 从失败节点重试
  - 保留原运行的用户输入；不重放旧 `_human_response` 或 `_rollback` 控制字段。连续模拟的精确状态续接使用检查点 rollback，而非把 retry 当成自动断点恢复。
- `POST /runs/{run_id}/rollback` 从指定事件检查点创建新运行
- `POST /runs/{run_id}/human-response` 提交人工回复并恢复
- `POST /runs/{run_id}/director-command` 向导演发送自然语言指令
- `POST /runs/{run_id}/stop` 请求停止单次运行
  - `pending/waiting_human` 会立即进入 `stopped`
  - `running` 先进入 `stopping`，当前模型响应完成后停止并保留已完成行动
  - Director 指令会在连续模拟的下一次角色行动前生效

## 3. 运行查询

- `GET /runs/{run_id}` 运行详情
- `GET /runs/{run_id}/status` 轻量状态：仅返回 `{"run_id":"run_xxx","status":"running"}`，不存在返回 404；不加载输入、输出和节点明细
- `GET /runs/{run_id}/events?after_seq=0&limit=500` 事件流
- `GET /runs/{run_id}/checkpoints?limit=80` 可回滚检查点
- `GET /runs/{run_id}/trace/{seq}` 单事件追溯（父事件、触发来源、上下文快照）
- `GET /runs/{run_id}/audit/verify` 审计链验证（hash 链完整性）
- `GET /runs/{run_id}/metrics` 运行指标
- `GET /compare-runs?run_a=...&run_b=...` 对比
- `POST /runs/{run_id}/report` 报告生成
  - 发送完整可读素材（含结尾和用户上传证据），不再只取前 16,000 字符。
  - 素材超过 `AUV_REPORT_SOURCE_MAX_CHARS`（默认 64,000 字符）时返回 413，在调用写作模型之前中止。可导出 Full Log，或确认模型上下文和成本后调整环境变量并重启；不会自动增加分段摘要调用。
- `POST /runs/{run_id}/media` 将报告、剧本或本次运行的可读输出转为图片或视频任务
- `GET /media/generations/{generation_id}` 查询并刷新异步媒体任务
- `GET /media/generations/{generation_id}/assets/{asset_id}/content` 读取本地生成资产或代理视频内容

后端重启会把上次未完成的 `pending/queued/running/stopping` 运行标为 failed，错误码为 `RUNTIME_INTERRUPTED`，并追加可追溯事件；不会自动重新入队。已完成输出和检查点保留，`waiting_human` 仍可通过人工回复接口继续。

## 4. 平台能力

- `GET /health` 健康检查
- `GET /model-config` 获取本地平台默认模型配置（只返回脱敏 key 状态）
- `PUT /model-config` 保存本地平台默认模型配置
- `POST /model-config/test` 测试模型连接
  - `provider=openai_compatible`：请求 `{base_url}/chat/completions`，支持云端 API、LM Studio、vLLM 等兼容服务
  - `provider=ollama`：请求 `{base_url}/api/chat`，默认本地地址为 `http://localhost:11434`
  - `provider=llama_cpp|lm_studio|vllm|localai|tgi|text_generation_webui`：使用各框架默认地址并映射到 OpenAI-compatible 协议
  - 本地框架，以及回环、私有局域网和 `.local` OpenAI-compatible 端点允许不填写 API Key
- `GET /media-config/{image|video}` 获取媒体模型配置
- `PUT /media-config/{image|video}` 保存媒体模型配置；API Key 只进入当前后端进程，不写入 SQLite
- `POST /media-config/{image|video}/test` 测试媒体端点
  - 图片：`openai_compatible` 或 `generic_http`
  - 视频：`openai_video` 或 `generic_http`
  - 环境变量密钥：`AUV_IMAGE_API_KEY`、`AUV_VIDEO_API_KEY`
- `GET /node-specs` 节点规格
- `GET /interaction-modes` 互动模式

连续执行沿用 Workflow JSON 的 `edges/interaction`，无需新增 API。实体间无边时共享公开场景，有边时按 source -> target 传递公开行动；双向交流需要反向边，连续模式允许通信环，DAG 仍拒绝环。节点运行输入的 `context.recent_actions` 是该节点的可见投影，`context.interactions` 和 `_interactions` 带互动规则、`edge_id`、源 `event_seq`，不包含其他节点私有认知。全局 Dynamic State 仍是共享的权威事实，不受消息连线隐藏。

运行详情、Full Log、Trace 继续保留本地用户可查看的完整审计证据，它们不是给 Agent 的上下文 API。前端实时/演播选项只改变展示节奏，不改事件查询、回放或运行协议。
- `GET /mode-contracts` 模式与子场景契约定义
- `GET /director/capabilities` 导演能力清单
- `GET /protocols/text-agent-v1` 外接协议声明
- `GET /templates` 模板列表
- `GET /templates/{id}` 模板详情
- `POST /templates` 模板创建/更新
- `POST /templates/compile` 计划文本自动解析并生成工作流
  - 响应包含 `simulation_blueprint`，并同步写入 `workflow.environment.simulation`
  - 可选参数 `submode`：在 `mode=research` 时支持 `simulation|research|consulting`
  - Director 会拆解 `assumptions/hypotheses/success_metrics/decision_criteria`
  - `simulation_blueprint` 包含 `research_design/simulation_design/output_plan/quality_controls`
  - 自动生成的 agent 节点会注入 `entity_type/responsibilities` 和关键拆解字段
- `GET /workflows/{workflow_id}/memories` 读取事实与角色历史记忆
  - 记忆字段仅包含 `kind=fact|character`；其他类型不会进入 API 响应或运行召回
  - 返回 `scope/subject/importance/confidence/source_event_seq`
- `DELETE /workflows/{workflow_id}/memories` 清空轻量记忆
- `GET /observability` 性能快照
- `GET /queue/status` 队列状态

## 4.1 调试与回滚

- 检查点来源：运行事件中的 `succeeded | waiting_human | failed | dynamic_state_updated`
- `GET /runs/{run_id}/checkpoints` 返回最近检查点：
  - `seq`：事件序号
  - `node_id`：检查点节点
  - `event`：检查点状态
  - `summary`：输出/错误摘要
  - `can_rollback`：是否允许回滚
- `POST /runs/{run_id}/rollback`
  - 请求：`{"event_seq": 12, "reason": "方向偏了，从这里重开"}`
  - 响应：`{"run_id": "run_xxx", "status": "queued", "source_run_id": "...", "event_seq": 12, "retry_from_node": "..."}`
  - 语义：创建一个新的 run，不修改原 run；DAG 复用检查点之前的上游输出，连续模拟从内部快照精确恢复当时的 Dynamic State、既有行动和 `scene_control`。`episode_audited` 边界也可作为回滚检查点。
  - 回滚输入会写入新 run 的 `input._rollback`，方便后续追溯。

## 5. 外接 Agent 接入模式

- `external_agent.integration_mode=http`：用户已有 API 直连
- `external_agent.integration_mode=bridge_local`：连接本地桥接器（默认 `http://127.0.0.1:8787`）
- 通用协议入口：`GET /protocols/text-agent-v1`
- 用户自己的 agent 需要暴露同步 HTTP 接口：
  - 默认路径：`POST /agent/tasks`
  - 请求核心字段：`task_id/run_id/node_id/context/input/constraints`
  - 响应核心字段：`task_id/status/output.text/metrics/errors`
  - `endpoint_url` 必须是有效的 `http/https` URL
  - 响应 `task_id` 必须与请求一致，否则本次节点执行失败

## 7. 关键节点补充

- 关键决策投票不是节点，而是导演机制。
  - 触发场景：重大调研判断、剧情关键分叉、角色结论冲突。
  - 事件类型：`director_vote_started/director_vote_cast/director_vote_finished`
  - 前端应展示为重大决策卡，而不是节点库里的可拖拽节点。

## 6. 关键返回状态

- Run 状态：`pending | running | waiting_human | stopping | stopped | succeeded | failed`
- Event 类型：`queued | running | waiting_human | human_resumed | director_overridden | director_corrected | director_vote_started | director_vote_cast | director_vote_finished | nodes_activated | episode_audited | context_pack_activated | dynamic_state_updated | dynamic_state_failed | stop_requested | stopped | succeeded | failed | skipped`
  - `nodes_activated.scene_contract`：首次路由建立的当前场景契约，后续路由沿用。
  - `episode_audited.progress_assessment`：按完整互动轮次记录 `round_progress`、连续 `stagnation`、`needs_attention` 和 `unassessed_rounds`；缺失判断不视作已通过。
  - `episode_audited.scene_control`：契约、停滞计数和审计游标；同事件包含独立的内部 `_rollback_snapshot`。这些控制信息不属于领域事实。角色 `succeeded.payload.output.turn_delta` 可为空，不直接写入 Dynamic State。
  - `nodes_activated`：AI 语义路由为当前阶段选择了相关自治节点。
  - `episode_audited`：Director 在节点完成提案后审计继续、转段或结束，不代替节点作出内容决策。
  - `context_pack_activated`：记录运行开始或转段时激活的背景条目、原因和估算 Token。
  - `dynamic_state_updated`：记录动态概念 Schema 与状态 Patch 的版本、准确行动/事件来源及接受/拒绝结果；公共事件只包含全局可见概念，内部回滚快照不会经事件 API 返回。
  - `dynamic_state_failed`：状态维护失败但节点行动已保留，运行可继续。
- Event 负载包含 `_trace`：
  - `trace_id`
  - `parent_event_ids`
  - `caused_by`（`scheduler|node|human|human_checkpoint|director|node_exception|routing`）
  - `context_snapshot`（裁剪后的执行上下文）
  - `prev_hash` / `event_hash`（防篡改链）
- `environment` 支持显式背景状态：
  - `profile`：整体背景
  - `scenario`：当前场景
  - `time_context`：时间背景
  - `spatial_context`：空间背景
  - `facts/constraints/glossary`：事实、约束、术语
  - `context_book.entries`：按场景激活的背景条目；支持 `kind`、`visibility`、`activation`、`priority`、`source` 和 `enabled`
  - `context_book.token_budget`：单个场景背景包预算，默认 `1600`
- `simulation_state.world_state` 会随运行事件写入：
  - `current_time/current_location`
  - `temporal_scope/spatial_scope`
- `simulation_state.runtime_metrics` 只包含机械执行指标：
  - `progress`
- `simulation_state.dynamic_state` 是当前领域事实的唯一权威来源。
- Full Log、Live、Trace 和 Replay 从 `run_events` 构建，不在 simulation state 内复制事件列表。
- 安全说明：
  - 事件与节点运行输入输出会对常见敏感字段自动脱敏（`api_key/token/authorization/password/secret`）
