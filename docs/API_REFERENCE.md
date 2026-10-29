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

连续模拟的结束结果现在明确区分执行结束和目标完成：仅 Director 返回 `decision=stop`、`task_complete=true` 且提供非空 `completion_reason` 时，运行状态为 `succeeded`。达到行动/轮数上限、或 Director 因无法推进而停止时，状态为 `stopped`，不是目标完成；异常仍为 `failed`。

`GET /runs/{run_id}` 的 `output.completion` 提供 `{status: completed|incomplete, reason, assessment?}`。reason 为 `goal_completed/director_stopped/action_limit/round_limit/user_stopped/execution_failed`。已有行动、状态与可用检查点保留；文学适配不得替 incomplete 结果补出不存在的结局。运行级终止事件仍写入签名事件流，但不作为可回滚的节点检查点。

`POST /templates/compile` 的结构化提取支持 `phases: [{name,objective,exit_criteria}]`，只覆盖用户请求的片段；无 phases 时使用模型 phase_logic，再缺失则使用单个任务阶段。`environment.time_context/spatial_context` 规范化后保留。不会根据故事关键词强制选择固定开场或死亡结局。

连续行动的 `action_checked` 事件新增 `check_attempt`：表示同一候选的核验协议尝试，独立于角色候选的 `attempt`。仅格式无效时最多重核验一次；每条记录的 `context_digest` 对应实际检查提示词，恢复不重放 Agent 行动。失败协议的 `diagnostic` 提供安全的字段类型/长度线索；既有审计侧响应摘录不进入可读广播或模拟事实。

检查记录协议版本为 3，结构合法的候选包含 `actor_id`，语义 assessment 必须带 `scope: {self_owned: boolean, public_only: boolean}`；缺失、类型错误或 accept 与 false 标记冲突都不得放行。scope 是检查模型的判断，不是外接 Agent 可以自行声明的授权。结构无效的响应不调用语义检查器，按下述错误协议记录。外接 Agent 的任务信封保持不变。

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
  - `narrative/story` 走生成与忠实度核验：正常两次模型调用，发现实质性补写时最多修正一次并再次核验，最多四次逻辑调用；底层有界传输重试另计。`briefing` 维持原有单次生成流程。
  - 作品允许改写对白、文风与非实质性细节，不允许新增未发生的答复、决定、风险事实、交易或结局。核验引用必须是素材与候选正文中实际存在的片段，语义判断仍由模型完成，并非形式化正确性保证。
  - 成功作品增加 `fidelity: {version, source_hash, checks}`，每项 check 含 `attempt/candidate_hash/decision/findings`；findings 引用 `candidate_quote/source_quote/reason`。该元数据不写回模拟事实，也不默认显示为正文。正式报告该字段为 null。
  - 未通过、无效核验、空白/截断输出或未配置模型返回结构化 502 `detail: {code,message,diagnostics}`；例如 `narrative_fidelity_failed/narrative_invalid_review/narrative_output_truncated/model_not_configured`。不会把失败候选或降级梗概当作合格文学作品返回，既有模拟状态不变。Provider 请求失败也返回安全的结构化错误，不透传原始响应。
- `POST /runs/{run_id}/media` 将报告、剧本或本次运行的可读输出转为图片或视频任务
- `GET /media/generations/{generation_id}` 查询并刷新异步媒体任务
- `GET /media/generations/{generation_id}/assets/{asset_id}/content` 读取本地生成资产或代理视频内容

后端重启会把上次未完成的 `pending/queued/running/stopping` 运行标为 failed，错误码为 `RUNTIME_INTERRUPTED`，并追加可追溯事件；不会自动重新入队。已完成输出和检查点保留，`waiting_human` 仍可通过人工回复接口继续。

连续循环异常时，运行详情 `output` 保留已接受的 `simulation`、完成的 `actions` 和 `scene_control`；`error` 包含 `type/message`，并追加 `run_control/failed` 审计事件。内部 `actions.received_event_seqs` 来自当轮实际输入，不是模型声明；该元数据不进入其他 Agent 的公开行动投影。

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

连续执行沿用 Workflow JSON 的 `edges/interaction`，无需新增 API。实体间无边时共享公开场景，有边时按 source -> target 传递公开行动；双向交流需要反向边，连续模式允许通信环，DAG 仍拒绝环。节点运行输入的 `context.recent_actions` 是该节点的可见投影，`context.interactions` 和 `_interactions` 带互动规则、`edge_id`、源 `event_seq`，不包含其他节点私有认知。全局 Dynamic State 是共享的领域记录，不受消息连线隐藏；它是当前状态的唯一存储权威，不等于每条来源声明已经被独立证实。

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
  - `round_progress` 每项另外包含 `goal_progress: advanced|none|regressed|uncertain` 与 `goal_reason`：分别判断新内容和接近场景退出条件，不能用自报 Delta、字数或新台词替代目标进展。缺失、无效、重复轮号或 uncertain 进入 `unassessed_goal_rounds`，不默认为通过。`goal_stagnation/goal_needs_attention` 只统计连续明确的 none/regressed。
  - 角色 `context.progress_feedback` 为运行层生成的场景反馈，连续两轮未接近目标时为 `refocus`，四轮为 `reassess`，六轮为 `consider_stop`；恢复进展或转场后清除。它不写入 Dynamic State、不指定角色行为、不制造外部事件，也不把停止标记为完成。路由和后续导演审计收到同一反馈。
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

### 连续模拟行动检查事件

现有 `GET /api/runs/{run_id}/events`、Trace 和 Full Log 新增 `event=action_checked`，不新增配置页面或 HTTP 入口。节点在检查期间仍为 running；只有接纳后才发 succeeded。

语义核验 `payload` 包含 `version=4`、`round`、`action_index`、`attempt`（1 或 2）、`candidate` 和 `assessment`。语义核验另有 `check_attempt/context_digest`；正常 assessment 为 `{decision: accept|conflict|uncertain, reason, scope: {self_owned, public_only}, findings: [{category, reason, evidence_ids}]}`，scope 两项必须为布尔值。category 为 `spacetime|existence|capability|knowledge|causality|evidence`。引用的既有依据快照在 `cited_evidence` 中，仍为未压缩原文；核验失败记录 `decision=error/error_type`，另有各最多 600 字符的 `response_excerpt/diagnostic`，检查传输异常对外失败消息不回显私有检查上下文。结构拒绝仍使用 v3 协议，不包含语义检查统计。

v4 的 `evidence_metrics` 只保存数值和分类：`evidence_count/aliases/prompt_chars/estimated_input_tokens/unprojected_input_tokens`，以及 `blocks` 下的 `stable_evidence/current_state/accepted_history/actor_input/candidate/envelope/system/protocol_recovery` 字符数与估算 Token。估算器是 CJK 字符加非 CJK 字符约四分之一，不是供应商 tokenizer。未压缩基准使用同一检查规则、原始重复证据和旧式 JSON 空格；协议重核验附加文本同时计入两侧。`provider_usage` 只摘录供应商实际返回的非负整数用量及缓存计数，不保存原始响应。预发送拒绝、预算阻断或网络异常也可能有准备输入的统计，不能将事件条数或估算数当作已计费请求；物理尝试仍由 Eval 共享预算记录。

检查请求按固定规则、完整稳定证据、当前状态/历史、角色实际输入、候选行动组织。仅精确重复且能节省空间的值使用行级 `refs: [{path, source, source_path}]`，将该行 value 对应的 null 还原为此前证据 value 的原值；空路径表示整个 value。只认程序生成的行级 refs，value 内同名用户字段仍是普通数据；引用有向无环，不跨请求、不改变原始证据 ID。所有启用背景条目、其他角色约束、私有审计状态、未编码行动和旧的实际送达消息均保留，不做关键词相关性裁剪、语义摘要或风险跳过。

角色输出先经过 `action_output.ParticipantAction` 结构校验。必需 `participation: act|observe|wait` 和字符串 `action`，act 时不能为空；`intent/observation/self_update/turn_delta/shared_effect_claim` 可省略，存在时必须为字符串。`episode_signal` 为 `continue|ready`，可选 `result` 为布尔值；不再根据旧别名补齐角色意图，也不把任意 `state_updates` 交给状态系统。坏 JSON、非对象、重复键、非有限数字及内置模型的 mock/截断响应不能回退成可读行动。允许 JSON 代码围栏。

结构错误写同一签名事件流：`candidate={}`、可计算时附 `response_digest`，assessment 为 `{decision: error, error_type: ActionOutputError, code: invalid_action_output, issues: [{field,type}]}`，不保存坏响应正文。内置节点至多重生成一次，与语义冲突修正共享两次候选总上限，不额外创建一套重试预算；传输/额度异常不在此吞掉。外接 Agent 不重发：`output.text` 普通文字通过显式适配成为公开行动，也可直接返回标准 participation/action 对象；以对象、数组或代码围栏开头的文字按结构化响应校验，不能借损坏 JSON 绕过检查。DAG 外接任务仍按既有 text 输出处理。

这些候选和引用可含角色私有内容，属于本地用户审计材料，不是其他 Agent 的 inbox。被拒绝的内容不会进入 NodeRun 的成功 output、近期有效行动、记忆、报告素材或 State Coder；未通过且修正次数耗尽时节点/run 为 failed，既有状态保留。外接 Agent 不自动重试，已经发生的远端副作用也不会因候选被拒绝而撤销。检查事件不是新增可回滚检查点，使用现有已提交边界回滚。

环境变量 `AUV_ACTION_CHECK_MAX_CHARS` 控制检查输入 JSON 字符限额，默认 `64000`。超出时明确停止，不裁掉背景规则后假装检查完成。当前门只用于 continuous 实体行动，非 DAG 工具调用权限网关；模型语义判断不是形式化一致性证明。

### Dynamic State 的来源语义

状态值写入后 `status=recorded`，并带 `evidence_kind: claim|observation|action|inference`，省略时保守采用 inference，非法枚举拒绝写入。claim 表示来源主体的断言，observation 表示其观察，action 表示接纳记录中的实际自身贡献，inference 表示 Coder 的推断；任何类别都不凭 confidence 或 source_refs 自动升级为已证实事实。类别由模型提出，程序只约束字段，不证明分类语义正确。

值及接纳历史保留准确 source_refs；既有类型、owner/visibility、保留期和回滚规则不变。角色上下文、State Coder 与通用节点提示词保留来源性质，避免把转述渲染为已验证结果。旧值的 confirmed 标签在状态规范化/读取投影中按 recorded 解释，缺失来源类别按 inference 解释，不改写原始历史事件或传入对象，也不引入第二个事实库或独立核实调用。

### State Coder 进度与语义索引

Dynamic State 新增执行层维护的 `last_coded_event_seq`，只引用当前分支实际接纳的行动事件，不是行动数组下标，也不是最近一次任意日志的序号。游标随内部回滚快照保存；缺少此字段时按 0 重新检查既有行动，不能因为行动已在列表中就假定已编码。模型返回同名字段不能控制执行游标。

`state_patch` 必须明确包含数组 `schema_ops/state_ops`；两个空数组表示已检查本批且无需持久化，不同于缺字段、坏 JSON、重复键、非有限数字或 mock/截断回复。运行路径按整批提交：任一操作拒绝时，状态、历史、版本和游标都不接受该批的部分结果。正常提交时，状态与游标在同一个签名检查点事件中落盘；数据库提交失败不提前发布新游标。

`dynamic_state_updated.payload.coding` 为 `{status: coded|pending, attempt: 1|2, last_coded_event_seq, through_event_seq, action_count}`。`changed` 只描述语义状态变化；有效空提案可以 `changed=false` 且进度前进。失败仅允许一次 State Coder 补编码，不重新执行 Agent、外部任务或 Director 的流程决策；再次失败抛出 StateCodingError，运行失败并保留已接纳行动、原状态和待编码检查点。传输与预算异常向上传递，不伪装为空提案，也不自动加预算。

概念 upsert 可选 `subject_id`（非空字符串，最长120字符）和 `predicate`（非空字符串，最长80字符）；缺省不建立该索引。subject 是被描述对象，不等同于私有 owner，可指物体等非 Agent 实体。字段一旦建立不可改指其他主体/属性，后续省略则保留；无索引的概念仍可用。内部 `query_dynamic_state(raw, subject_id=..., predicate=..., node_id=...)` 返回精确匹配的 `{concept_id: {schema,current}}`，先按 owner 过滤私有概念；没有新 HTTP 入口、状态数据库或领域枚举。

Checker 的既有证据列表保留 `dynamic.*` 当前记录和 `actor.input` 实际认知，新增 `state.coding` 游标与全部未编码事件 ID；`event.*` 是最近12条与全部待编码公开行动的去重并集。未编码部分不受12条窗口截断，超出既有检查字符限额则明确失败。完整检查视图只给审计模型，角色输入仍按通信和私有状态规则过滤。执行前的确定性校验只核对实际 actor、事件引用/正文、消息通道和状态可见性，不从自由 predicate 或自然语言资源描述推导可执行禁令；合法输入仍经过原语义 Checker。

### 自动解析的失败协议

`POST /api/templates/compile` 成功响应提供 `generation: {attempts, recovered_errors, format: compact|core, output_limit}`。只在模型给出有效 goal 与具名 roles 后构建工作流；不再因网络、截断或 JSON 错误返回启发式成功草案。

失败返回 HTTP 502，`detail` 为 `{code, message, diagnostics}`。code 包括 `network_error/timeout/http_error/model_not_configured/output_truncated/invalid_json/invalid_plan_schema`。结构失败 diagnostics 保留每次 attempt、finish_reason 和 response_chars；供应商错误保留 provider_status 与 retryable，不向网页回显原始模型正文、Key 或请求头。网页 ApiError 保留这些字段，但只显示 message。预算异常继续向调用者传播，不伪装成可修复的 JSON 失败。

格式/截断最多重新生成一次，传输重试由 Provider 负责；真实 Eval 的传输重试在预算层执行并逐次计数，不能无限重试或自动放宽额度。

规划模型只返回语义核心：模式、目标、角色自身利益/任务及必要认知边界、公共初始状态、完成条件、表达形式和简短约束。显式 roleplay/custom 不要求调研字段；research/auto 仅在实际研究时可补充一个简短 research 对象。Python 负责补节点/边/ID、默认语言、默认数量、标准运行契约及 DTO 外壳，不编造缺失角色或事件。

首次失败后用独立的 core 提示词替换请求：删除环境/假设等可选部分和通用长说明，角色仅要求身份、目标、实体类型，必要的私有认知/边界仍可保留；不把失败正文或完整旧 Schema 附回重试。原始 plan_text 与证据完整保留在两种提示词中。编译后原文逐字存入 `environment.context_book.entries[id=source_plan]`：私有、面向 director_1、priority=0、always=false，不消耗普通背景激活预算，不广播给角色；Checker 沿用其完整启用背景证据读取此条。表达与公共背景使用提取结果，不再把原文前220/500字符作为共享语境冒充完整计划。

后端 `AUV_PLANNING_OUTPUT_TOKENS` 默认2400（512–4000），核心恢复最多1600；与节点输出限额独立。Provider 使用局部副本设置该次额度，不修改共享模型配置。Eval 可用 `--planning-output-tokens` 指定规划额度，但所有阶段、恢复和物理请求仍共享原总调用/Token/估算费用上限；没有新增前端配置项或自动加钱逻辑。
