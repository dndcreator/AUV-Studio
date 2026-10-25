# 系统架构说明

最后更新：2026-10-22

## 1. 总体架构

- 前端：React + TypeScript + React Flow + Zustand + React Query + i18next/react-i18next
- 后端：FastAPI + SQLAlchemy + SQLite
- 执行：后端真实执行，前端轮询事件/状态

## 2. 核心模块

1. Workflow Editor（前端）
- 画布与节点编排
- 参数面板
- 环境引擎面板
- 运行时多语言：`i18next/react-i18next`，语言资源位于 `frontend/src/i18n/locales`
- `canvasPresentation.ts` 只把当前可见运行事件投影成节点显示状态，回放和实时画布使用同一入口，不向工作流配置写入 UI 状态。
- `components/SimulationNode.tsx` 独立渲染预设实体图标、名称、双语运行状态和 React Flow 连接点；技术节点不强加实体图标。
- `styles/studio.css` 承载工作室外观、控件层次和响应式规则，加载于共享组件样式之后；保持既有黄色电视风格，不混入业务规则。

2. Execution Engine（后端）
- DAG 校验与拓扑顺序
- 节点调度与上下文注入
- 失败处理与重试
- 连续运行采用模式无关的分布式自治协议：语义路由只唤醒相关节点，节点自行决定参与、观察或等待
- Director 只审计共享状态、阶段边界和偏移，不替节点决定行动

### 双契约运行上下文

- `task_contract` 位于 `environment.simulation`，描述目标、过程类型、交付物格式与风格、共享背景、约束和完成条件。
- `identity_contract` 位于每个实体节点的 `config`，描述身份、自身目标、已知与未知、能力和边界。
- 每轮节点输入由共享任务契约、节点身份契约、节点记忆和当前宏观状态组成。
- 节点输出包含参与判断、自身行动、意图、观察、自身状态变化、共享影响声明和阶段就绪信号；影响声明必须经后续状态审计，不能直接成为世界事实。

### 模块边界

- `service.py`：工作流、运行查询、报告、审计等应用服务入口，保留既有公共导入路径。
- `plan_compiler.py`：自然语言计划解析、双契约生成和工作流编译。
- `mode_contracts.py`：模式契约定义。
- `model_config_service.py`：云端与本地模型配置、保存和连接测试。
- `media_service.py`：视觉提示词规划、图片/视频 Provider 调用、异步任务刷新和媒体资产读取。
- `execution/engine.py`：执行循环、节点调度和跨领域编排，不承载图规则、事件存储、快照格式、Director 纯策略或记忆策略。
- `execution/graph.py`：DAG 校验、拓扑排序、条件路由、后代范围和边互动消息。
- `execution/memory.py`：`fact|character` 记忆的持久化、回滚裁剪、召回评分、节点筛选和 Director 干预读取。
- `execution/events.py`：运行事件脱敏、审计哈希链写入、历史节点输出恢复和人工恢复事件。
- `run_lifecycle.py`：中断状态与审计事件的原子持久化，供队列异常/停机和启动恢复共同调用；不重新执行任务，也不改动模拟领域状态。
- `runtime_lock.py`：文件型 SQLite 数据库的 OS 级单实例租约。先获租约，再建表/恢复，最后启动队列；租约覆盖整个服务生命周期，异常退出由 OS 释放。单进程内可运行多个异步队列 Worker。
- `execution/checkpoints.py`：节点上下文快照、连续模拟检查点和回滚恢复。
- `execution/director_policy.py`：确认策略、质量评估、纠偏文本、重大决策识别和投票解析；不访问数据库或 Provider。
- `execution/scene_control.py`：Scene Contract 规范化、导演审计提示和按完整轮次计数的停滞控制；不访问数据库或 Provider。
- `execution/simulation_state.py`：无副作用的模拟状态初始化与转换。
- `execution/context_book.py`：场景级背景激活、预算裁剪、节点知识隔离和激活追踪。
- `execution/output_utils.py`：执行输出文本归一化。
- 前端模型元数据、配置弹窗、项目库和运行覆盖层均为独立模块；`App.tsx` 负责组合、画布和跨模块状态协调。
- `components/RunOverlays.tsx`：证据、全量实况和运行结果覆盖层，保持展示与运行控制解耦。
- `hooks/useLiveBroadcast.ts`：Live 可读事件映射、播放游标、广播快照和播报队列。
- `hooks/useRunPolling.ts`：按运行隔离请求、15 秒超时/取消、串行轮询、事件游标和去重；每轮最多读取五页、每页 500 条。只有补齐尾部事件才发布终态；指标/检查点通常间隔至少 5 秒读取，终态额外刷新。可选遥测失败不丢弃已读取的事件。
- `components/StudioIcon.tsx`：工作室操作图标；`styles/studio.css`：工作室外观与响应式布局。样式按 React Flow、共享样式、工作室样式的顺序加载，实体预设通过 CSS 变量控制外观。

### 执行层依赖方向

`engine -> graph | events | checkpoints | memory | director_policy | scene_control | simulation_loop | simulation_state | nodes`

领域模块不得反向导入 `engine`；数据库与 Provider 同时参与的流程编排保留在 `engine`，纯转换和单一持久化职责下沉到对应模块。

3. Event & Observability
- run_events（节点事件）
- run_metrics（运行指标）
- observability（HTTP+运行性能）
- queue_status（队列与并发 worker）
- `GET /api/runs/{run_id}/status` 只查主记录 ID/状态列，避免每秒加载完整运行快照。执行失败按持久化状态计入队列失败数；等待均值按已开始任务计算。
- Worker 未处理异常及正常停机通过统一生命周期函数落库并保持审计链。状态更改与审计事件同事务提交；错误落库失败被记录到服务日志，Worker 继续处理后续任务。重启在单实例租约下对遗留活动运行执行幂等失败恢复，不自动重新入队；不承诺数据库不可用时仍能保存事件或恢复未持久化响应。
- API 采用浏览器 Origin 显式允许列表，无 Origin 的本地客户端保持可用；证据上传流限额和 Office 内部展开限额先于解析，解析在线程池执行。

4. Provider & Protocol
- OpenAI-compatible provider（云端 API，以及 llama.cpp、LM Studio、vLLM、LocalAI、TGI、text-generation-webui 等本地兼容端点）
- Ollama 原生 `/api/chat` provider；本地回环地址与 Ollama 支持无 API Key 连接
- external_agent 文本协议 v1.0
- 外接方式：`http` 直连或 `bridge_local` 本地桥接
- Media Provider：OpenAI-compatible 图片、OpenAI 异步视频、Generic HTTP Adapter
- 媒体生成是运行后的独立管线：可读模拟结果 -> 视觉镜头提示词 -> 媒体 Provider -> 资产

5. Memory
- SQLite 轻量记忆表
- 自动写入与下游注入
- Full Log、事实记忆和角色记忆保存可追溯历史；它们不充当当前世界的权威状态。

6. Dynamic State Coder
- 连续模拟使用固定的最小元协议和运行时动态概念，不预设感情、阵营、研究假设等领域字段。
- State Coder复用 Director阶段审计，批量读取尚未编码的节点行动并返回 `schema_ops + state_ops`，不增加独立的 State Coder调用；最终轮始终执行一次边界审计以避免遗漏尾部状态。
- 新概念必须说明持续性与未来相关性；程序层限制总概念数、单实体概念数、每轮新增数、值深度和历史长度。
- `value_type` 是写入约束；类型不匹配的值被拒绝。`retention` 使用 `persistent | until_resolved | rounds`，其中 `rounds` 到期自动退休。
- 每个 Schema/State 操作必须引用本批行动中的准确 `node_id + action_index + event_seq`。概念创建后，scope、owner、visibility 和 value type 不可被模型改写。
- 私有状态只能由 owner 自己的行动建立或更新，并且始终只注入 owner 节点。
- 连续模拟启用 Dynamic State 时必须存在 Director。Director 提供阶段审计调用，程序约束层决定 Patch 是否可接受。
- 普通节点只收到全局概念与属于自己的私有概念；路由器和前端事件不接收私有概念内容。
- 所有接受和拒绝的 Patch均进入 `dynamic_state_updated` 事件。内部检查点保存完整状态，公共事件删除私有回滚快照。
- 连续回滚同时裁剪源运行在检查点之后写入的记忆，避免未来信息泄漏到新分支。

### 状态层职责

- `dynamic_state`：连续模拟中唯一的领域事实与演化概念层。
- `world_state`：时间、空间和环境坐标，不保存任意领域事实。
- `runtime_metrics`：只保存执行进度等机械指标，不保存风险、置信度、关系或结论。
- `memories`：事实历史和角色经历的召回材料，不等同于当前权威状态。
- 角色包使用 `character_history`，不再把最后一条角色记忆标记为当前状态。
- `run_events`：完整过程、Live、Trace 和 Replay 的唯一时序来源，不在 simulation state 中复制 timeline。
- 状态规范化采用 v2 字段白名单；废弃字段不会被读取、迁移或回写，旧项目需显式转换后再运行。

7. Context Book
- `environment.context_book` 保存模拟开始前成立的背景、规则、观点、传言、假设与方法。
- 每个场景生成稳定的 Context Pack，转段时刷新，节点行动时按可见范围过滤。
- Context Book 不保存运行中发生的事件；领域变化进入 Dynamic State，时间/空间坐标进入 world state，历史经历进入 fact memory 和 character memory。
- Evidence 保留原始来源与研究资料，不自动转化为无来源的背景事实。
- 内置节点只接收自身可见条目；外接 Agent 默认不接收 Context Book。

8. Evaluation
- `app/evaluation/` 提供固定场景加载、预算 Provider、规则评分和独立的长程依赖检查点构建。
- `app/execution/entity_contracts.py` 在后端定义最小实体行为契约，保证导入、自动生成和手动创建的节点不依赖前端默认提示词才能保持个体、群体、组织、环境、事件和物体语义。
- `app.eval_runner` 在独立内存数据库中运行真实模型 Eval，不污染产品数据。
- Eval Provider 对调用数、Token、单次输出和估算费用执行运行前检查，并禁用自动重试。
- 普通 Eval 每个场景仅保存一份最新 Markdown，包含实际生成内容、简短评语和用于比较的精简元数据，不复制原始 Full Log；独立长程评测的产物规则见其专项文档。
- `app/narrative.py` 统一产品文学成稿与评测的提示词；用户的体裁/文风优先于默认小说形式，不改变研究节点本身的行为契约。
- `app/evaluation/writing.py` 加载版本化写作配置，固定素材后分别检查小说和剧本；评分标准与生成要求分离，五项 0-4 分由代码汇总，不能用接口成功分数代表文学质量。
- `evals/styles.json` 是写作基准配置，`writing_styles` 不运行模拟；素材、要求、评分规则或评审模型变化后，不继续作跨次分差比较。产品仍允许用户输入任意文风，不受评测预设限制。

## 3. 状态机（Run）

`pending -> running -> (succeeded | failed | waiting_human)`

- `waiting_human`：由 human_checkpoint 触发
- 人工回复后回到 `queued/pending` 并恢复执行

## 4. 数据持久化

- `workflows`：工作流定义
- `runs`：运行主记录
- `node_runs`：节点级运行详情
- `run_events`：事件流
- `workflow_templates`：模板
- `memories`：轻量记忆
- `media_config`：媒体 Provider 元数据，不保存 API Key
- `media_generations`：媒体任务状态、镜头提示和资产引用

## 5. 扩展策略

1. 节点扩展：通过 node-spec schema 描述前端表单与后端语义
2. Provider 扩展：统一 DTO，差异下沉到 provider 层
3. 协议扩展：外接 Agent 保持版本化协议

### 报告素材边界

报告使用完整可读行动与用户证据，不再按字符前缀截断。完整素材超过 `AUV_REPORT_SOURCE_MAX_CHARS` 时，在 Provider 调用前返回 413；预算内仍只有一次写作调用，不自动创建摘要/裁判调用。Briefing 的运行统计与素材分开，避免重复把每个行动摘录一遍。该字符限额不是模型上下文容量或账单保证，调用重试仍遵循 Provider 策略。
# Distributed Autonomous Execution

AUV continuous execution is mode-neutral. Roleplay scenes, research stages, consultation workstreams, and custom collaborations use the same distributed loop:

1. An AI semantic router reads the compact shared context and node definitions, then activates only nodes relevant to the next episode.
2. Each activated node reads the shared context plus its own role state and memory, and autonomously emits an action or proposal.
3. Each turn reports a possibly empty `turn_delta` claim. Actual actions remain the evidence; Delta does not become a durable fact or a separate timeline.
4. During the existing boundary audit, a separate State Coder output dynamically creates, updates, or retires only concepts that can constrain future behavior.
5. Director manages a Scene Contract (`goal`, `pacing`, `detail`, `progress_scale`, `exit_conditions`) and audits episode boundaries. It returns `continue`, `transition`, or `stop`, and a semantic progress judgment for each completed global round. Runtime counts consecutive stagnant rounds; two rounds trigger consideration of pacing changes or closure without forcing character actions.
6. Every accepted domain state value cites a concrete node action and event. Dynamic State remains the sole domain authority. `_scene_control` contains only runtime control metadata; checkpoints separately restore its contract, stagnation counter, and audit cursor alongside Dynamic State and action cursors. Audited boundaries are rollback-capable.

The engine hard-codes only the protocol envelope. Relevance, proposals, semantic state summaries, and phase meaning remain model-decided; there are no roleplay keyword tables or domain-specific routing rules.

Scene setup is returned in the initial existing routing call, and progress checks share the existing Director/State Coder audit call. There is no extra per-turn judge call. Contracts stay stable within a scene; Director may tune pacing/detail/scale, and a transition can replace the goal and exits. Fine detail is selective rather than a checklist, action limits are ceilings rather than quotas, and output quality does not reward word count.
