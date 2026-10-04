# 系统架构说明

最后更新：2026-10-04

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
- `execution/engine.py`：执行编排、事件生命周期和节点调度。
- `execution/simulation_state.py`：无副作用的模拟状态初始化与转换。
- `execution/context_book.py`：场景级背景激活、预算裁剪、节点知识隔离和激活追踪。
- `execution/output_utils.py`：执行输出文本归一化。
- 前端模型元数据、模型配置弹窗和项目库弹窗均为独立模块，`App.tsx` 只负责组合与状态协调。

3. Event & Observability
- run_events（节点事件）
- run_metrics（运行指标）
- observability（HTTP+运行性能）
- queue_status（队列与并发 worker）

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
- `variables`：进度、风险等执行遥测，不作为模拟事实来源。
- `state_memory`：旧 DAG 工作流兼容层；连续执行不再向它写入领域状态。
- `memories`：事实日志与角色经历的召回材料，不等同于当前权威状态。

7. Context Book
- `environment.context_book` 保存模拟开始前成立的背景、规则、观点、传言、假设与方法。
- 每个场景生成稳定的 Context Pack，转段时刷新，节点行动时按可见范围过滤。
- Context Book 不保存运行中发生的事件；动态变化分别进入 world state、fact memory 和 character memory。
- Evidence 保留原始来源与研究资料，不自动转化为无来源的背景事实。
- 内置节点只接收自身可见条目；外接 Agent 默认不接收 Context Book。

8. Evaluation
- `app/evaluation/` 提供固定场景加载、预算 Provider、规则评分和独立的长程依赖检查点构建。
- `app/execution/entity_contracts.py` 在后端定义最小实体行为契约，保证导入、自动生成和手动创建的节点不依赖前端默认提示词才能保持个体、群体、组织、环境、事件和物体语义。
- `app.eval_runner` 在独立内存数据库中运行真实模型 Eval，不污染产品数据。
- Eval Provider 对调用数、Token、单次输出和估算费用执行运行前检查，并禁用自动重试。
- 结果保存为本地 JSON 和 Markdown，供历史比较和人工复核。

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
# Distributed Autonomous Execution

AUV continuous execution is mode-neutral. Roleplay scenes, research stages, consultation workstreams, and custom collaborations use the same distributed loop:

1. An AI semantic router reads the compact shared context and node definitions, then activates only nodes relevant to the next episode.
2. Each activated node reads the shared context plus its own role state and memory, and autonomously emits an action or proposal.
3. Public changes are appended to a compact natural-language shared state; private changes remain in that node's rolling character/state memory.
4. During the existing boundary audit, a separate State Coder output dynamically creates, updates, or retires only concepts that can constrain future behavior.
5. Director does not author node decisions. It audits episode boundaries and returns only `continue`, `transition`, or `stop`, plus a compact shared-state summary.
6. Every accepted state value cites a concrete node action and event. Internal Dynamic State snapshots restore the exact schema, values, visibility, and provenance during rollback.

The engine hard-codes only the protocol envelope. Relevance, proposals, semantic state summaries, and phase meaning remain model-decided; there are no roleplay keyword tables or domain-specific routing rules.
