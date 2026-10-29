# 系统架构说明

最后更新：2026-10-25

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
- `execution/perception.py`：连续模拟的公开行动投影、有向消息可见性、连线 inbox 和节点专属模板/运行上下文；不存储第二份状态或事件。
- 实际节点输入生成 `received_event_seqs`，附在现有内部行动记录中，供路由/审计判断消息是否被提供；它不证明模型已理解或外部世界已收到。公开行动投影不转发收件元数据。缺失记录是未知，显式空列表才表示该轮没有提供其他节点的行动。
- 角色包和路由描述保留明确的功能 `role`；`context.communication` 给出模式、入站/出站边、本人职责及相邻节点名称/ID，不自动把别人的真实角色标签当成公开身份。阶段审计获得同一通信拓扑，不能只靠全量行动历史假定消息已送达。私有档案不加入已知邻居元数据。
- `execution/memory.py`：`fact|character` 记忆的持久化、回滚裁剪、召回评分、节点筛选和 Director 干预读取。
- `execution/events.py`：运行事件脱敏、审计哈希链写入、历史节点输出恢复和人工恢复事件。
- `run_lifecycle.py`：中断状态与审计事件的原子持久化，供队列异常/停机和启动恢复共同调用；不重新执行任务，也不改动模拟领域状态。
- `runtime_lock.py`：文件型 SQLite 数据库的 OS 级单实例租约。先获租约，再建表/恢复，最后启动队列；租约覆盖整个服务生命周期，异常退出由 OS 释放。单进程内可运行多个异步队列 Worker。
- `execution/checkpoints.py`：节点上下文快照、连续模拟检查点和回滚恢复。
- `execution/director_policy.py`：确认策略、质量评估、纠偏文本、重大决策识别和投票解析；不访问数据库或 Provider。
- `execution/scene_control.py`：Scene Contract 规范化、导演审计提示和按完整轮次计数的停滞控制；不访问数据库或 Provider。
- `execution/action_evidence.py`：检查证据投影、精确值引用去重、稳定前缀与输入分块估算；不访问数据库或 Provider，不做语义相关性裁剪。`action_consistency.py` 负责结构接纳后的六类语义核验、有限修正和审计记录。
- `evaluation/diagnostics.py`：从既有事件汇总检查分块用量和紧凑的逐轮进展，不复制提示词、私有正文或建立第二条时间线。
- 测试/评测层：`scripts/verify.py` 统一离线工程门禁；pytest显式分层并隔离用户数据、禁止真实HTTP。`behavior_eval.py` 使用现有模块入口、共享BudgetedProvider与独立Judge，`evaluation/targets.py` 装配目标，`judging.py` 固定判据，`benchmarks.py/acceptance.py` 管理校准、基线和质量状态。均不加入产品正常运行循环，详见 `docs/TESTING.md`。
- 审计同时读取最小角色职责表与收件证据，不再只看连线/叙述推断知情。职责匹配和完成条件仍由模型判断，不新增领域关键词规则；仅要求提案的任务不应被扩张成执行和回执。
- `execution/simulation_state.py`：无副作用的模拟状态初始化与转换。
- `execution/context_book.py`：场景级背景激活、预算裁剪、节点知识隔离和激活追踪。
- `execution/output_utils.py`：执行输出文本归一化。
- 前端模型元数据、配置弹窗、项目库和运行覆盖层均为独立模块；`App.tsx` 负责组合、画布和跨模块状态协调。
- `components/RunOverlays.tsx`：证据、全量实况和运行结果覆盖层，保持展示与运行控制解耦。
- `hooks/useLiveBroadcast.ts`：Live 可读事件映射、播放游标、广播快照和播报队列。
- `livePlayback.ts`：按事件间隔及积压计算有界演播节奏；`components/LiveControls.tsx` 提供双语实时/演播切换。默认同步最新事件，演播只影响展示，不影响运行调度或完整时间线。
- `hooks/useRunPolling.ts`：按运行隔离请求、15 秒超时/取消、串行轮询、事件游标和去重；每轮最多读取五页、每页 500 条。只有补齐尾部事件才发布终态；指标/检查点通常间隔至少 5 秒读取，终态额外刷新。可选遥测失败不丢弃已读取的事件。
- `components/StudioIcon.tsx`：工作室操作图标；`styles/studio.css`：工作室外观与响应式布局。样式按 React Flow、共享样式、工作室样式的顺序加载，实体预设通过 CSS 变量控制外观。

### 执行层依赖方向

`engine -> graph | perception | events | checkpoints | memory | director_policy | scene_control | simulation_loop | simulation_state | nodes`

领域模块不得反向导入 `engine`；数据库与 Provider 同时参与的流程编排保留在 `engine`，纯转换和单一持久化职责下沉到对应模块。

3. Event & Observability
- run_events（节点事件）
- run_metrics（运行指标）
- observability（HTTP+运行性能）
- queue_status（队列与并发 worker）
- `GET /api/runs/{run_id}/status` 只查主记录 ID/状态列，避免每秒加载完整运行快照。执行失败按持久化状态计入队列失败数；等待均值按已开始任务计算。
- Worker 未处理异常及正常停机通过统一生命周期函数落库并保持审计链。状态更改与审计事件同事务提交；错误落库失败被记录到服务日志，Worker 继续处理后续任务。重启在单实例租约下对遗留活动运行执行幂等失败恢复，不自动重新入队；不承诺数据库不可用时仍能保存事件或恢复未持久化响应。
- 连续循环的路由/节点/审计/预算异常保留已接受的 Dynamic State、已完成行动和场景控制，并追加终态失败事件。保留行动不等于其声明已成为事实；精确恢复仍以既有检查点为依据。
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
- State Coder复用 Director阶段审计，批量读取尚未编码的节点行动并返回 `schema_ops + state_ops`，正常路径不增加独立调用；只有编码失败允许一次独立补编码，仍受原预算约束。最终轮执行边界审计，未编码失败不能冒充成功完成。
- 新概念必须说明持续性与未来相关性；程序层限制总概念数、单实体概念数、每轮新增数、值深度和历史长度。
- `value_type` 是写入约束；类型不匹配的值被拒绝。`retention` 使用 `persistent | until_resolved | rounds`，其中 `rounds` 到期自动退休。
- 每个 Schema/State 操作必须引用本批行动中的准确 `node_id + action_index + event_seq`。概念创建后，scope、owner、visibility 和 value type 不可被模型改写。
- 写入状态统一为 `recorded`，以 `evidence_kind=claim|observation|action|inference` 标记来源性质，缺省为 inference；非法类别拒绝写入。持久化接纳、来源可定位和事实已被证实是不同语义，confidence 不提供独立核实。模型负责分类含义，代码不宣称证明其准确性。读取旧 confirmed 标签时按 recorded 解释，不改写原始事件。
- 私有状态只能由 owner 自己的行动建立或更新，并且始终只注入 owner 节点。
- 连续模拟启用 Dynamic State 时必须存在 Director。Director 提供阶段审计调用，程序约束层决定 Patch 是否可接受。
- 普通节点只收到全局概念与属于自己的私有概念；路由器和前端事件不接收私有概念内容。
- 所有接受和拒绝的 Patch均进入 `dynamic_state_updated` 事件。内部检查点保存完整状态，公共事件删除私有回滚快照。
- 连续回滚同时裁剪源运行在检查点之后写入的记忆，避免未来信息泄漏到新分支。
- `state_coding.py` 管批次校验、事件游标和有界补编码，Engine 仅负责提交签名事件与发布状态。`last_coded_event_seq` 随 Dynamic State 检查点恢复，不依赖列表长度；只从当前分支行动账本取增量，不扫描其他运行的后续事件。部分失败整批不提交；显式空数组才表示无需持久化。状态和游标同一检查点提交，失败保留未编码行动。
- `subject_id/predicate` 是可选且稳定的检索元数据，不是另一套事实，也不是执行规则。按实体/属性查询先应用 owner 可见性；描述对象与私有信息所有者分开。无固定属性词表、图数据库、额外 confirmed 标记或常规模型调用。

### 状态层职责

- `dynamic_state`：连续模拟中唯一的领域记录与演化概念层；存储权威不等于对所有转述或推断的真值认证。
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
- Eval Provider 对调用数、Token、单次输出和估算费用执行运行前检查；关闭底层隐藏重试，由预算层最多重试一次临时 Provider 错误，每次物理请求计入原预算，失败请求保守预留 Token/费用。
- 普通 Eval 每个场景仅保存一份最新 Markdown，包含实际生成内容、简短评语和用于比较的精简元数据，不复制原始 Full Log；独立长程评测的产物规则见其专项文档。
- `app/narrative.py` 统一产品文学成稿与评测的提示词；用户的体裁/文风优先于默认小说形式，不改变研究节点本身的行为契约。
- `app/narrative_fidelity.py` 是报告链路内的独立模块，不是新服务/节点/状态存储。先生成作品，再对实际素材检查新增实质性事实；最多一次修正及复核。失败不发布，引用必须匹配原始片段；作品核验不改变 Dynamic State 或角色行动。产品与固定写作基准共用此路径，正常两次、修正最多四次逻辑调用，Eval 的物理请求始终受同一预算限制。正式 briefing 维持原流程。
- 行动一致性接纳将“协议失败”与“语义冲突”分开：协议失败可对同一候选重核验一次，不重发角色或外接副作用；语义冲突维持既有的一次内置角色修正上限。每次实际核验单独审计、检查输入限额并计入 Eval 预算，不以恢复为由放宽事实约束。
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

报告使用完整可读行动与用户证据，不再按字符前缀截断。完整素材超过 `AUV_REPORT_SOURCE_MAX_CHARS` 时，在 Provider 调用前返回 413。Briefing 保持一次生成；文学改写按上文忠实度门正常两次、最多四次逻辑调用，不因素材过长自动创建分段摘要。该字符限额不是模型上下文容量或账单保证，调用重试仍遵循 Provider 策略。
# Distributed Autonomous Execution

AUV continuous execution is mode-neutral. Roleplay scenes, research stages, consultation workstreams, and custom collaborations use the same distributed loop:

自动计划的阶段由模型按用户请求范围给出，由 `app/plan_phases.py` 规范化；不再为每种模式强塞完整流程。时空字段原样传入现有坐标系统。连续内置节点由引擎一次组装 context/role，通用节点层校验归属后不再重复追加；DAG 路径仍使用通用上下文渲染。

1. An AI semantic router reads the compact shared context and node definitions, then activates only nodes relevant to the next episode.
2. Each activated node reads the shared context plus its own role state and memory, and autonomously emits an action or proposal.
3. Each turn reports a possibly empty `turn_delta` claim. Actual actions remain the evidence; Delta does not become a durable fact or a separate timeline.
4. During the existing boundary audit, a separate State Coder output dynamically creates, updates, or retires only concepts that can constrain future behavior.
5. Director manages a Scene Contract (`goal`, `pacing`, `detail`, `progress_scale`, `exit_conditions`) and audits episode boundaries. It returns `continue`, `transition`, or `stop`, and a semantic progress judgment for each completed global round. Runtime counts consecutive stagnant rounds; two rounds trigger consideration of pacing changes or closure without forcing character actions.
   Goal progress is assessed independently as advanced/none/regressed/uncertain against the contracted exit conditions and progress_scale. Runtime exposes generic refocus/reassess/consider_stop feedback after two/four/six consecutive none/regressed rounds to the router, participants and the existing audit. Missing, uncertain or duplicated assessments cannot prove progress or a stagnation streak. Micro progress can coexist with slow pacing and high detail. Feedback never selects actor outcomes, creates deadlines or forces completion; an incomplete stop remains incomplete.
6. Every accepted domain state value cites a concrete node action and event. Dynamic State remains the sole domain authority. `_scene_control` contains only runtime control metadata; checkpoints separately restore its contract, stagnation counter, and audit cursor alongside Dynamic State and action cursors. Audited boundaries are rollback-capable.

`execution/completion.py` 统一执行器和评测的目标完成判据，不把 stop 或达到上限当作完成。Director 报告 task_complete/completion_reason；无法确认目标完成则保留结果并 stopped。completion 属于运行结果，不是第二套领域状态。节点与审计获得剩余执行额度，不能借此替参与者指定结局。Scene Contract 缺失控制项时采用中性 meso，明确的 micro 与高细节仍保留。

行动核验 v3 保留对 self_owned/public_only 的模型判断并强制检查布尔协议，另在语义核验前拒绝无效角色响应；公开动作中的其他角色新台词/反应和私有心理都应拒绝。此门不采用人物名或剧情词的硬编码检测，也不承诺模型绝不漏判。检查投影通过已存在的事件与状态引用去重，超出全局窗口但实际送达的旧消息仍保留正文；Director 与 State Coder 共用一次行动正文。

The engine hard-codes only the protocol envelope. Relevance, proposals, semantic state summaries, and phase meaning remain model-decided; there are no roleplay keyword tables or domain-specific routing rules.

Scene setup is returned in the initial existing routing call, and progress checks share the existing Director/State Coder audit call. Progress assessment itself adds no per-turn call; action admission has a separate consistency check described below. Contracts stay stable within a scene; Director may tune pacing/detail/scale, and a transition can replace the goal and exits. Fine detail is selective rather than a checklist, action limits are ceilings rather than quotas, and output quality does not reward word count.

### 连线、感知与执行顺序

- 无实体间连线：共享公开场景。存在实体间连线：只沿 source -> target 传递行动；反向通信需要反向连线，连续模拟允许环，DAG 仍禁止环。Director/summary 的边不触发实体消息网络模式。
- 连续回合的 inbox 使用现有边规则渲染消息，带 `edge_id/event_seq`；`condition` 根据行动显式返回的布尔 `result` 筛选。`required` 不作为连续循环的硬依赖屏障，严格任务依赖仍由 DAG 路径承担。
- 连线控制节点行动/消息的直接可见范围，不隐藏已被审计接受的全局 Dynamic State。路由器选择激活节点不等于让它们自动知道彼此行动；Agent 仍可基于自身目标和已知事实独立行动。
- 公开投影仅允许行动、参与方式、主体及轮次/事件元数据。`intent/observation/self_update/turn_delta/shared_effect_claim` 不转发给其他节点；本人可读取自己的这些记录，Director/State Coder 可读取审计证据。私有状态继续由 Dynamic State owner 过滤；角色历史只召回给所属节点。
- 连续模式的表达式解析、system/user prompt 模板与外接 Agent 请求都使用同一受限上下文，不能通过引用其他节点的输出绕过投影。共享背景激活查询也不读取私有认知字段。
- Director 的共享指导和场景契约被提示不得转述未公开的秘密；这层语义约束不能被字段投影证明，模型若把秘密写进公开 `action` 仍可能泄露。面向当前本地单用户，Full Log/Trace 仍是有权查看私有证据的审计视图，不是给所有 Agent 公开的聊天频道。
- 单次模拟仍逐节点 await，后续节点可见先前已完成且获准投递的本轮行动。队列 Worker 并发的是不同 run。独立节点并行需要另外设计同轮快照、冲突合并及取消语义，本次没有开启。

### 行动接纳与一致性

`action_boundary.py` 在模型执行前检查 Engine 组装的实际 actor、事件引用与正文、消息通道及私有状态投影。它只判断可执行的元数据约束，不从自由属性名称或自然语言世界状态推导资源/物理规则；合法输入继续经过原有语义审查。没有因确定性预检查而跳过正常 Checker 调用。

`execution/action_consistency.py` 是进程内模块，不是新服务、节点或状态系统。连续模拟先生成候选，再检查，最后才写 `succeeded`、投递给其他角色、写记忆及进入 State Coder。传统 DAG 执行和最终报告写作不经过这个门；本模块也不撤销外接 Agent 已执行的外部副作用。

`execution/action_output.py` 单独负责角色行动结构与外接文本适配。先校验 JSON/字段再调用语义检查器，坏响应不会自动降级为正文或默认行动。participation/action 必填，act 正文非空，私有意图可为空而不编造；额外 actor state_updates 不进入持久化。内置结构修复与语义修复共享最多两个候选；外接任务仍只执行一次。结构失败通过同一 action_checked v3 记录安全字段错误和响应摘要，不记录坏正文、不调用第二个模型替代格式校验。

- 固定六类检查原则：时空、实体存在/当前状态、能力/资源/权限、知识与通信边界、因果与结果归属、既定设定/研究证据。具体规则来自用户设定和实际状态，不写领域词表、物理引擎或人物性格评分，不做内容分级。
- 检查器读取初始环境、完整启用背景条目、实体定义、当前世界坐标、含私有内容的 Dynamic State、最近12条与全部未编码公开行动的去重并集，以及行动者实际获得的 context/role。state.coding 明确标记当前状态与待编码增量，不截掉较早的待编码事件。规则不受背景激活关键词和正文预算影响；背景 belief/rumor/assumption 必须保留其不确定性。已编码的旧事实仍需由已有状态/记忆机制保留，检查不是无限历史检索保证。
- 发送端将稳定证据放在前面，候选行动放在最后；仅用指向此前证据值的精确 refs 去重，不抽取关键句、删去未激活规则或隐藏其他实体限制。角色实际输入的引用与 visible_dynamic_ids 仍单独表示其知情边界，不因审计器看到更多原文就扩大角色权限。原始证据保留用于引用核验和 cited_evidence；线上的精确投影及别名理解仍需真实模型复测，不由 JSON 可还原性推出语义准确率。
- `action_checked` v4 记录分组字符数/估算 Token 和供应商返回的数值用量。协议重核验重新计算实际提示指纹和新增开销；统计不含原文，也不改变统一预算。稳定前缀仅创造服务端缓存的条件，不保证缓存命中或40%-60%的真实节省。没有引入轻量模型、风险分类服务或免检自然语言通道。
- 检查器是有权审计私有材料的模型调用，沿用平台 Provider/default model；不会把完整检查上下文发给其他角色或外接 Agent。传输配置不进入证据投影。模型输出 `accept/conflict/uncertain`，非接受判断必须引用实际证据 ID；代码强制验证格式/引用存在性，但不声称证明引用的语义正确。
- 尝试、提议、说谎和误解本身可以是有效行动；不能自动等同于成功、他人同意或验证过的研究结论。已建立的例外可以改变有效行动范围。未知细节不自动构成冲突；实质性结果缺少必要依据时不把它接纳为已发生事实。
- 内置 LLM 节点最多重新生成一次并重新检查；修正通知是固定通用文本，不复制审查者看到的秘密或理由。外接 Agent 不自动重发。仍冲突/不确定、无效检查输出、超时或预算异常时停止为可检查的失败，保留先前行动和权威状态，不自动放行、改写角色行动或无限重试。
- 每次检查写入已有 `run_events` 的 `action_checked`，保留候选、判断、所引证据快照和检查输入摘要指纹；成功事件通过 trace parent 指向对应检查。候选不是有效行动或新的回滚检查点。原始技术证据可由本地用户在 Events/Trace/Full Log 查看；Live、可读过程与报告素材只使用已接纳输出。
- 状态消费端保留 evidence_kind/source_refs/confidence；通用节点不再把缺少认识论标记的值渲染成 Authoritative Dynamic State。State Coder 仍复用现有 Director 审计，没有新增核实服务或状态层。
- 每个候选增加一次逻辑模型调用；一次修正另增一次角色调用和一次检查，Provider 原有有限传输重试策略不变。Eval 的统一调用/Token/费用估算预算覆盖这些调用，不自动增加预算。检查 JSON 输入超过 `AUV_ACTION_CHECK_MAX_CHARS`（默认 64000）时明确失败，不静默裁掉规则；这是工程字符限额，不是供应商上下文容量或账单保证。
- 目标进展判断复用既有边界审计，无独立评审调用。默认逐轮审计的调用次数不变；非默认低频审计在已观测到连续缺少目标进展后可能提前下一次边界审计，仍受相同预算限制。`goal_stagnation` 是可回滚的 `_scene_control` 运行元数据，不属于 State Coder 的长期事实。

这道门减少矛盾的提前传播，不能保证模型永不误判或漏判。大规模场景的检查上下文、逐候选调用成本和真实模型语义效果仍需单独验收。

### 计划解析错误与重试

`plan_prompt.py` 生成按显式模式收束的紧凑语义请求，并把语义核心补成既有编译 DTO。roleplay/custom 不要求调研设计，auto/research 的研究信息只在适用时可选；模型负责目标、角色利益/认知边界、初始局面、完成条件及表达要求。ID、默认数量/语言、节点连接、标准结构由程序生成。截断/格式失败使用另一份真正更小的 core 请求，不再给完整旧 Schema 追加一句“简短一点”。

原始请求逐字归档为已有 Context Book 中的私有 source_plan 条目，不直接进入角色共享语境，也不参与普通激活预算；一致性审计仍可读取完整原文。公共环境与角色私有认知由提取结果分别承载，普通节点不自动获得导演持有的全部原始材料。没有新增世界模型、状态存储或内容限制。

规划输出额度独立于角色输出：平台默认2400，core 恢复最多1600。OpenAI-compatible/Ollama Provider 的 with_output_limit 返回局部副本，避免并发编译修改共享限额；BudgetedProvider 的阶段副本共享同一 usage/retries/category 账本，只改变该请求输出预留，总预算不变。

`plan_generation.py` 负责结构化计划的解析、最小参与者结构验证及有界重新生成，`plan_compiler.py` 只做模式/工作流构建。网页 `/api/templates/compile` 和后端真实验收调用同一个编译函数，不存在“后端测试专用”的草案生成逻辑。

无效 JSON、`finish_reason=length/max_tokens` 和缺少有效 goal/roles 分别记录为 `invalid_json/output_truncated/invalid_plan_schema`，最多重新生成一次紧凑完整 JSON，不自动提高输出限额。请求/预算异常保留原异常链并向上传递，不再返回启发式草案；无 Key 的 mock 回复明确报 `model_not_configured`。日志记录类型、供应商状态、结束原因及响应字符数，不保存角色私密正文或凭据。成功返回 `generation.attempts/recovered_errors`；耗尽后给前端结构化 502 错误，API 客户端仅显示 message，保留 code/diagnostics 供程序处理。

Provider 原有有限传输重试与计划重新生成职责分离；400/401/402/403/404/422 等不可恢复错误不盲重试，预算异常不被当成格式错误再次调用。真实 Eval 在计费层统计每次物理尝试，不能用内层隐藏重试绕过额度。自然语言验收先由模型生成工作流，再施加测试轮数/行动/输出限额，不预写角色行动；编译失败也保存一份精简结果。

连续模拟的报告素材仅采用成功实体输出和用户证据，不再把派生 summary 当作新的事实来源；DAG 的最终汇总仍可作为产物素材。文学适配明确保留未回答问题与未决定事项，不能为收尾代替角色回应或成交。此提示词约束不是语义证明；真实样本仍发现越界补写，人工验收不能被成功状态或规则分数替代。
