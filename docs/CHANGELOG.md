# 变更记录

本文件用于持续记录 AUV 的产品能力、接口、前端入口和重要实现决策，避免后续遗忘上下文。

## [Unreleased]
### Long-Horizon Dependency Evaluation

- Added an opt-in 60-turn evaluation for World Book activation, private visibility, durable node memory, distributed participation, and context-budget stability.
- Added a zero-cost deterministic simulation mode and a separate real-model mode that calls only four sparse checkpoints instead of every turn.
- Added early-commitment, delayed keyword activation, private-route isolation, group-node visibility, and late-decision probes.
- Added strict real-run ceilings of 8 calls, 40,000 tokens, 1,000 output tokens per call, and $0.25 estimated cost; defaults are substantially lower.
- Kept the evaluator outside ordinary pytest and product startup paths; preflight remains the default and model calls require explicit `--execute`.
- Added local JSON and Markdown review artifacts, a manual long-term-behavior checklist, documentation, and regression coverage.
- Completed the first real long-horizon run with 4 calls, 3,805 tokens, and an estimated $0.006304; early commitments influenced the final individual decision and private knowledge remained isolated.
- Human review found a group node echoing its instruction instead of producing group behavior and a broad semantic hint overactivating `BELL-9`; added explicit instruction-echo and background-overactivation checks and narrowed the fixed scenario trigger.
- Moved minimum entity behavior semantics into a backend runtime contract so group nodes retain collective behavior even when imported or created without a frontend `behavior_prompt`.
- Applied the entity contract to continuous simulation, built-in Agent prompts, external-agent context, and long-horizon probes; custom behavior remains a specialization layer.
- Defined group output around majority/minority distribution, uncertainty, subgroup movement, observable behavior, social pressure, rumors, silence, and anonymous representative remarks while rejecting single-person inner life and instruction echoing.
- Added a reproducible continuous story-quality scenario with two individuals, one heterogeneous crowd, private knowledge, physical constraints, six-to-eight autonomous actions, and an observable closure condition.
- Stopped continuous simulations from redundantly recalling full peer fact memories already represented by recent public actions and shared state; ordinary DAG collaboration keeps peer-fact recall unchanged.
- Added bounded action-length instructions to the story-quality fixture after the first run exposed truncated JSON and excessive repeated context.
- Verified the group runtime contract with a real continuous story run: the crowd split into evacuating, force-open, and undecided subgroups and produced collective actions instead of instruction echoing or single-person behavior.
- The optimized story run produced seven valid autonomous actions across all three actors with no action-contract failures, using 12 calls, 48,640 tokens, and an estimated $0.059464 before the fixed 50,000-token budget prevented the final closure and synthesis calls.
- Human review rated the partial scene strong in causal progression, spatial action, dialogue, and group differentiation, while identifying missing closure and one contradictory Director state-summary clause as remaining issues.

### Real-Model Quality Evaluation

- Added an opt-in fixed-scenario Eval runner that uses the currently configured real model without joining the ordinary test suite.
- Added a roleplay regression scenario covering autonomous actors, state-changing conflict, continuity, Director boundaries, and final outcome fidelity.
- Added hard call, estimated-token, per-response output, and estimated-cost budgets with safe CLI ceilings and preflight-only defaults.
- Disabled Provider retries during Eval so logical call limits match actual HTTP attempts.
- Isolated Eval runs in an in-memory SQLite database and prohibited tool, external-agent, and human-checkpoint side effects.
- Sanitized Provider URLs in Eval artifacts and excluded credentials, query strings, and fragments.
- Added conservative token accounting for failed provider requests and clearer empty-content diagnostics for reasoning models that exhaust their output limit before producing a final answer.
- Added Eval-only DeepSeek non-thinking mode for bounded short structured actions while leaving normal product inference behavior unchanged.
- Tightened the distributed action contract to prioritize complete JSON and one focused contribution over uncontrolled verbosity.
- Added deterministic action-contract, participation, completion, and repetition checks plus an optional one-call advisory LLM Judge.
- Added local JSON and Markdown review packets, previous-run comparison, a manual review checklist, documentation, and regression tests.
- Validated the fixed roleplay scenario against the configured DeepSeek endpoint: 8 calls, 16,774 tokens, and an estimated $0.022552 under conservative price assumptions; human review identified an unresolved final turn, which scores 83.3/100 under the added completion check.
- Calibrated the opt-in Eval defaults to 26,000 total tokens, 600 output tokens per call, and a $0.10 estimated-cost ceiling based on the controlled run.
- Kept broad credential redaction while allowing only numeric token telemetry fields to remain inspectable in traces.
- Added scenario-level Director completion verification so a technically successful run cannot receive a perfect score after merely exhausting its round limit with unresolved terms.
- Extended the fixed scenario horizon from two to three rounds while retaining the four-action ceiling, giving the responding actor one bounded opportunity to close terms before evaluation ends.

### Image And Video Generation

- Added post-run image and video generation from a completed report, narrative, or readable simulation output.
- Added OpenAI-compatible image generation, asynchronous OpenAI video jobs, and a generic HTTP media adapter for local or third-party frameworks.
- Added a compact Results media studio with image/video switching, style, size, shot count or duration, provider setup, previews, and automatic job refresh.
- Added persistent media provider metadata and generation history while keeping media API keys only in environment variables or backend process memory.
- Added provider error normalization, local image asset storage, video content proxying, and generation status polling.
- Hardened external-agent HTTP integration with URL validation, readable transport errors, and request/response `task_id` correlation.
- Added backend tests for image, video, generic adapter, secret storage, and external-agent validation, plus frontend API coverage.
- Replaced raw provider selection with OpenAI, compatible API, and local generator presets; OpenAI setup now requires only a Key by default.
- Moved Base URL, model overrides, generation paths, status paths, and optional local authentication behind context-sensitive fields and one Advanced disclosure.
- Combined connection testing and persistence into one Test & Save action and routed an unconfigured Generate action directly into setup.
- Added media protocol compatibility validation and normalized common `data/assets/images/videos/output/result` response envelopes.
- Added a step-by-step media setup and troubleshooting guide while keeping explanatory copy out of the product UI.
- Bound process-memory media secrets to their Provider and Base URL so changing endpoints never forwards an existing Key to a different service.

### Scene Context Book

- Upgraded the environment engine with a portable `context_book` embedded in workflow, template, Seed, and project JSON.
- Added simple background entries with global or selected-node visibility while keeping trigger, priority, linking, and budget details out of the primary UI.
- Added Director plan decomposition for background, rules, beliefs, rumors, assumptions, methods, instructions, and private role knowledge.
- Added scene-stable context packs with Chinese-friendly phrase matching, one-hop linked entries, priority ordering, and token-budget trimming.
- Injected only node-visible entries into built-in agent prompts and recorded entry IDs and activation reasons in tamper-evident traces.
- Added `context_pack_activated` events at run start and episode transitions.
- Kept Context Book, world state, three-layer memory, and Evidence as separate runtime concerns.
- Removed the full Context Book from runtime environment payloads; external agents do not receive Context Book entries by default.
- Added backend coverage for compatibility, Chinese activation, linked entries, visibility isolation, budget ordering, prompt injection, and trace events; added frontend persistence coverage.

### Core Module Decomposition

- Extracted natural-language plan compilation and workflow generation from `service.py` into `plan_compiler.py`.
- Moved mode contracts and model configuration lifecycle into `mode_contracts.py` and `model_config_service.py` while preserving existing service imports and HTTP APIs.
- Extracted pure simulation-state transitions and output text normalization from `WorkflowEngine` into focused execution modules.
- Moved model provider metadata, the model configuration modal, and the saved-project library modal out of the frontend root component.
- Kept UI behavior unchanged and added no explanatory feature copy during the component split.
- Verified the refactor with 68 backend tests, 27 frontend tests, and a successful production build.

### Local Model Providers

- Added native Ollama `/api/chat` support with keyless local connections and `llama3.2` as the UI preset.
- Added persisted presets for llama.cpp Server, LM Studio, vLLM, LocalAI, Hugging Face TGI, and text-generation-webui through the shared OpenAI-compatible adapter.
- Extended keyless local detection to private LAN addresses and `.local` hosts for models running on another machine.
- Enabled keyless OpenAI-compatible calls for loopback endpoints such as LM Studio and vLLM while preserving remote no-key mock behavior.
- Added provider selection to the model setup wizard and per-node advanced model overrides.
- Added provider-aware connection testing, authentication status, response parsing, and backend coverage for both local protocols.
- Prevented saved cloud API keys from being forwarded when users switch provider type or endpoint; local provider switches clear the previous endpoint's secret.

### Shared Task And Identity Contracts

- Added a Director-generated shared task contract covering objective, process type, deliverable format/style, background, constraints, and completion condition.
- Added per-node identity contracts covering identity, objective, knowledge, unknowns, capabilities, and autonomy boundaries.
- Replaced the writer-room-oriented node proposal protocol with mode-neutral participation, self-owned action, intent, observation, self-state, and shared-effect claims.
- Made output richness follow the requested deliverable while explicitly preventing nodes from controlling other participants or inventing unobserved global facts.
- Marked node effects as claims until Director audit confirms them in the compact shared state, while preserving legacy output aliases for integrations and old run records.

### Mode-Neutral Distributed Autonomy

- Replaced fixed all-node continuous rotation with AI semantic activation of only the nodes relevant to the next process episode.
- Generalized the runtime from roleplay turns to a mode-neutral distributed protocol shared by simulation, research, consulting, collaboration, and custom workflows.
- Added autonomous node outputs for action, proposal, compact public-state change, private rolling-state change, and episode-boundary readiness.
- Restricted Director to continuity and macro-boundary audit (`continue | transition | stop`) after node decisions, rather than authoring node actions.
- Added compact natural-language shared context, active proposals, and public updates without adding a graph database or vector-retrieval dependency.
- Added trace events for semantic node activation and episode audit while keeping backstage control events out of the user-facing Live broadcast.

### Saved Project Library

- Renamed the primary persistence action to Save Project to distinguish SQLite persistence from JSON export.
- Added a bilingual Open Project entry in the product top bar backed by the existing workflow list/detail APIs.
- Added a searchable saved-project library with project name, ID, version, and last-updated time.
- Reset stale run UI state when another saved project is opened and disabled project switching during an active run.

### Run Stop And Live Intervention

- Added `POST /api/runs/{run_id}/stop` with cooperative per-run stopping for queued, active, and human-waiting runs.
- Preserved completed actions and simulation state when an active run stops; the worker emits a traceable `stopped` event after the current provider response returns.
- Added Stop controls to both the product top bar and canvas Studio Dock, replacing Run while a simulation is active and showing a bilingual stopping state.
- Connected Director overrides to the continuous simulation loop so new natural-language guidance is applied before the next entity action instead of only being stored.
- Added backend API/engine coverage and frontend API coverage for the stop path.

### Maintainability Pass

- Reviewed frontend and backend module sizes; identified `App.tsx`, `styles.css`, backend `service.py`, and execution `engine.py` as the main future refactor targets.
- Extracted the canvas Studio Dock into `frontend/src/components/StudioDock.tsx` to reduce `App.tsx` UI responsibility without changing behavior.
- Refactored backend agent entity prompt assembly into small helper functions in `execution/nodes.py`, keeping builtin LLM and external-agent prompt formats compatible.
- Verified backend with `python -m pytest backend/tests -q`, frontend build with `npm.cmd run build`, and standalone frontend tests with `npm.cmd run test:run`.
### Global Detail Granularity

- Consolidated entity few-shot guidance back into each entity `behavior_prompt` so preset metadata stays prompt-native and simple.
- Added global simulation `detail_granularity` with `concise` and `detailed` modes in the creation settings.
- Added entity-specific detailed expansion instructions at runtime: individual, group, organization, environment, event, and artifact each receive different detail guidance when detailed mode is enabled.
- Preserved `detail_granularity` in backend simulation state and included it in rendered simulation context for traceability.
- Verified backend with `python -m pytest backend/tests -q`, frontend build with `npm.cmd run build`, and frontend tests with `npm.cmd run test:run`.
### Lightweight Entity Action Examples

- Added editable `action_examples` to each world-entity preset as a lightweight few-shot action contract rather than a complex rule engine.
- Injected entity action examples into both builtin LLM agent execution and entity nodes using external-agent execution mode.
- Added frontend and backend tests to verify action examples are persisted into entity config and included in runtime prompts.
- Verified backend with `python -m pytest backend/tests -q`, frontend tests with `npm.cmd run test:run`, and standalone frontend build with `npm.cmd run build`.
### Backend Resume And Trace Test Cleanup

- Fixed human resume traceability by emitting `human_resumed` at resume-run start and preserving historical upstream outputs during retry/resume routing.
- Fixed human-response queue-full handling so a run remains `waiting_human` if the resume task cannot be queued.
- Replaced the obsolete vote-node engine test with a director-vote mechanism test that asserts `director_vote_finished` events instead of a `vote` node.
- Reworked secret-redaction coverage to test actual runtime node input paths and assert sensitive values do not appear in persisted event or node-run JSON.
- Verified backend with `python -m pytest backend/tests -q` and frontend with `npm.cmd run test:run` plus `npm.cmd run build`.
### Entity-First Node Creation

- Added a small decoupled frontend entity-definition module for real-world simulation entities: Individual, Group, Organization, Environment, Event, Artifact, and Human Gate.
- Changed the manual create drawer to present Add Entities as the default user-facing creation model, while keeping low-level developer nodes only in Custom mode.
- Moved External Agent from a default visible node type to an advanced execution mode on entity/agent nodes.
- Added basic behavior prompts per entity type so the node knows its broad real-world behavior boundary without introducing a complex rule engine.
- Added backend compatibility so agent nodes with `execution_mode=external_agent` reuse the existing AUV Text Agent Protocol path.
### Drawer Scroll And Selection Editor Clarity

- Renamed the user-facing Inspector entry to Edit Selected / 编辑所选 to clarify that it edits the currently selected node, selected edge, or global environment.
- Added localized helper copy explaining what the selection editor does in node, edge, and empty-selection states.
- Standardized drawer scrolling by disabling sticky menu headers inside studio drawers and keeping close controls anchored to the drawer corner.
### Create Drawer Scroll Polish

- Changed the Create drawer step tabs from sticky positioning to normal in-flow content so the step header scrolls together with the creation form.
- Added a lighter guided-step visual treatment for active Create sections to reduce the feeling of a long backend-style menu.
### Director Cat Mascot Polish

- Reworked the Director mascot into a cleaner original 2D black cat with softer proportions, gold eyes, cream muzzle, broadcast scarf, idle float, blink, and tail animation.
- Isolated the mascot CSS from older Director orb span rules so future visual tweaks can happen without disturbing the Director command bubble behavior.
- Verified with 
pm.cmd run build and 
pm.cmd run test:run.
### Drawer Separation And Localization Polish

- Split the right studio drawer into distinct Inspector and Advanced panels: Inspector now shows only selected node/edge/global editing, while Advanced shows runtime, Live, intervention, memory, monitor, trace, metrics, report, performance, and queue tools.
- Added bilingual labels for the most visible Advanced drawer controls and empty states that were previously hardcoded in English.
- Improved Advanced drawer visual hierarchy with a channel-style header, section treatment, and stronger readable hints under the Yellow Broadcast theme.
- Verified with `npm.cmd run test:run` and a standalone `npm.cmd run build`.
### Director Cat Avatar

- Replaced the Director floating orb with an original animated 2D black cat avatar, including ears, face, eyes, nose, tail, idle bobbing, blinking, and tail motion.
- Kept the Director command bubble behavior unchanged while making the Director feel more like a character companion on the canvas.
- Verified with `npm.cmd run build` and `npm.cmd run test:run`.
### Canvas Scroll Fix

- Changed React Flow wheel behavior so the main canvas no longer captures mouse-wheel scrolling for zooming or panning; page scrolling remains available while zoom is handled through canvas controls.
- Restyled the Inspector, Advanced, and Model dock buttons to use the same high-contrast yellow-card treatment as the rest of the Yellow Broadcast UI.
- Verified with `npm.cmd run build` and `npm.cmd run test:run`.
### Canvas Usability Polish

- Disabled React Flow wheel zoom on the main simulation canvas and enabled wheel panning to avoid accidental zoom when users try to scroll the canvas.
- Reworked the canvas background from a flat black field into a layered yellow TV-signal stage with radial glow, scanlines, channel grid, vignette, and subtle signal rings.
- Verified with `npm.cmd run build` and `npm.cmd run test:run`.
### Guided Create Flow

- Fixed yellow-highlight readability by forcing high-contrast text colors for primary/success buttons, active dock pills, chips, and yellow cards.
- Changed the Create drawer from a long scrolling menu into a guided stepper: Auto, Roles, Manual, and Templates.
- Default Create now opens on Auto plan generation, while role cards, manual node library, templates, and external-agent setup remain available through step tabs.
- Verified with `npm.cmd run build` and `npm.cmd run test:run`.
### Yellow Broadcast Visual Direction

- Shifted the frontend visual language to an original yellow-black 2D broadcast/TV-channel identity inspired by retro TV signal aesthetics: bold yellow panels, black ink borders, CRT scanlines, channel-style labels, skewed cards, and comic-like shadows.
- Replaced the generic brand mark with an original 2D AUV-TV screen logo treatment built in markup/CSS.
- Reframed the hero copy as a channel/tuning metaphor to match the simulation-as-broadcast product concept.
- Restyled the canvas, dock, Live broadcast window, Director orb, drawers, node cards, forms, and React Flow nodes under the unified Yellow Broadcast system.
- Verified with `npm.cmd run build` and `npm.cmd run test:run`.
### Canvas-First Studio UX

- Reoriented the frontend shell around a central React Flow simulation canvas instead of a persistent three-column dashboard.
- Converted the node/template creation area and inspector/advanced panels into on-demand studio drawers controlled from an in-canvas dock.
- Added a persistent canvas Live window for key event broadcast and quick trace inspection from broadcast lines.
- Added a floating Director orb with an inline natural-language command bubble so Director acts as a lightweight canvas companion rather than a normal node panel.
- Kept existing workflow, run, director, template, monitoring, trace, report, and external-agent functionality intact while changing the default presentation to a canvas-first AI Simulation Studio.
- Verified with `npm.cmd run build` and `npm.cmd run test:run`.
### Frontend Product Polish

- Added a product-grade mission strip above the workflow canvas to make model readiness, simulation mode, and run status visible before users enter the detailed editor.
- Reworked the topbar brand treatment with a visual mark and stronger product header hierarchy.
- Added a late CSS polish layer for a more polished simulator-console feel: richer atmospheric background, HUD cards, rounded side panels, stronger canvas stage styling, improved React Flow node styling, and more tactile buttons/forms.
- Verified the visual refresh with `npm.cmd run build` and `npm.cmd run test:run`.
### Local Startup Validation

- Validated Node.js upgrade to `v24.18.0` and npm `11.16.0` on the local machine.
- Refreshed frontend dependencies with `npm.cmd install`, verified `npm.cmd run test:run`, `npm.cmd run build`, and `npm.cmd audit` with `0 vulnerabilities`.
- Updated `run.py` to prefer `npm.cmd` on Windows so PowerShell execution-policy blocks on `npm.ps1` do not break one-click startup.
- Verified launcher readiness with `.venv\Scripts\python.exe -c "import run; run.check_ready()"`.
### Launcher Robustness

- Added a Node.js version preflight check to `run.py` so the launcher fails before starting services when Node.js is below the Vite-required `20.19.0+` / `22.12.0+` range.
- Documented the `TypeError: crypto.hash is not a function` startup failure as a Node.js version issue in the local runbook.
### Product Closure Updates

- Added a local model configuration API and frontend setup wizard so users can configure OpenAI-compatible base URL, default model, and API key before running simulations.
- Model API keys are stored in local backend SQLite config and are not returned to the frontend in plaintext or embedded into workflow/template exports.
- Changed Auto Parse Plan UX from immediate canvas overwrite to a director draft confirmation flow; users must explicitly apply the generated workflow draft.
- Added a frontend External Agent Interface card and updated the Text Agent Protocol docs so users know how to connect their own local HTTP agent or bridge adapter.
- Updated API docs to remove the obsolete vote-node description; voting is now documented as a director-triggered mechanism.
### Dependency Security

- Upgraded frontend `vitest` to `^4.1.10` and added an npm `overrides.esbuild` pin to `^0.28.1`, reducing `npm audit` from critical/high findings to `0 vulnerabilities`.
- Verified frontend `npm run test:run`, `npm run build`, and `npm audit` after the dependency security update. The current local Node.js `18.19.0` still emits an engine warning; use Node.js `20.19+` or `22.12+` for normal development.

### Product Direction

- 产品定位逐步从“Agent 工作流可视化”扩展为“可玩的多角色模拟器/研究模拟器”。
- 核心体验明确为：输入设定，生成角色和协作结构，由导演调度与纠偏，过程可广播、可干预、可追溯，结果可回放、可报告、可复现。
- 保留三类模式：
  - `roleplay`：剧情/角色扮演/高自由度模拟。
  - `research`：研究、调研、咨询，包含 `simulation/research/consulting` 子场景。
  - `custom`：用户完全自定义节点、关系与执行流程。

### Workflow And Node System

- 前端使用 React + TypeScript + React Flow，后端使用 Python + FastAPI，存储使用 SQLite。
- 工作流 JSON 支持 `nodes/edges/entry_nodes/environment`。
- 节点类型包括：
  - `director`
  - `agent`
  - `external_agent`
  - `human_checkpoint`
  - `prompt`
  - `tool`
  - `condition`
- 边支持 `interaction` 语义：
  - `mode`: `report/instruction/feedback/dialogue/handoff`
  - `relation`: 角色关系，如 `peer/leader/member`
  - `template`: 互动消息模板
  - `required`
  - `intensity`
- 前端支持节点库、画布连线、节点配置、边互动配置、保存、加载、导入、导出。
- 手动快捷创建支持 Planner/Coder/Reviewer/Human Checkpoint。
- 前端移除 `Vote Node` 入口，投票不再作为节点，而作为导演机制触发，避免节点体系混乱。

### Director Layer

- 新增导演节点 `director`，用于全局目标、风格约束、GPRO 候选优化和运行纠偏。
- 新增导演控制台：
  - `GET /api/director/capabilities`
  - `POST /api/runs/{run_id}/director-command`
- 导演指令支持自然语言入口，作用范围包括：
  - `global`
  - `phase`
  - `node`
- 导演指令事件 `director_overridden` 纳入审计 hash 链。
- 新增导演预执行确认机制 `environment.simulation.preflight_confirm`：
  - 在进入高 token 成本阶段前暂停。
  - 展示导演预览。
  - 用户确认后继续执行。
- 人工确认恢复会提交 `metadata.kind=director_preflight_confirm`，恢复后跳过重复确认闸门。
- 重大分歧投票改为导演机制：在检测到关键决策冲突时，导演可触发投票事件流：
  - `director_vote_started`
  - `director_vote_cast`
  - `director_vote_finished`

### Auto Planning And Templates

- 新增计划文本自动解析：
  - `POST /api/templates/compile`
  - 支持从自然语言计划生成工作流、角色、背景和 simulation blueprint。
- Auto Parse Plan 前端面板支持：
  - 直接粘贴计划文本。
  - 上传 `.txt/.md` 文件。
  - 选择 `auto/research/roleplay/custom`。
  - 在 `research` 下选择 `simulation/research/consulting`。
  - 控制最大 agent 数。
- 自动解析结果包含 `simulation_blueprint`，并写入 `workflow.environment.simulation`。
- 导演 LLM 自动解析会推断 `simulation.semantics`：
  - `individual`
  - `cohort`
  - `mixed`
  - `workstream`
- `simulation.semantics` 包含 `mode/confidence/reason`，用于平滑区分少节点角色模拟与多节点大众模拟。
- `simulation.semantics` 会渲染进节点的 Simulation State 提示词，供后续 agent/director/external_agent 使用。
- 新增模板机制：
  - `GET /api/templates`
  - `GET /api/templates/{id}`
  - `POST /api/templates`
- 模板可保存完整 workflow，包括角色卡、环境、seed、模式和节点关系。
- 新增 `Simulation Seed`：
  - 自动解析生成的 simulation blueprint 默认带 `seed`。
  - 前端支持展示、复制、重生成。
  - seed 存在 `workflow.environment.simulation.seed` 中，随模板复现。

### Modes And UI Isolation

- 新增前端模式隔离开关：
  - `research`
  - `roleplay`
  - `custom`
- 模式存储在 `workflow.environment.simulation.ui_mode`。
- 不同模式会批量切换右侧观测与控制面板，降低界面复杂度。
- 前端页面根节点新增 `theme-${uiMode}`，为后续按模式做视觉差异预留。
- Mode Switch 区展示导演对当前模拟语义的推断结果和置信度。
- Auto Parse Plan 生成后展示 `Director Pre-Parse` 说明，并允许用户一键覆盖为 `individual/cohort/mixed/workstream`。

### Role Cards And Roleplay Controls

- 新增 `Role Cards` 配置区，持久化到 `workflow.environment.simulation.role_cards`。
- 角色卡字段包括：
  - `name`
  - `archetype`
  - `goal`
  - `style`
  - `boundaries`
- 少节点场景下，鼠标 hover 节点会显示角色悬浮卡。
- hover 匹配规则：
  - 优先用节点 `config.role` 匹配角色卡 `name`。
  - 次级用节点 label 包含角色卡 `name` 匹配。
- `roleplay` 模式新增负面提示词/边界控制：
  - 存储为 `workflow.environment.simulation.roleplay_boundaries`。
  - 前端支持按行编辑保存。
- 暂未实现角色头像/立绘/徽章，后续保留为单独设计。

### State, Memory, And Simulation Engine

- 新增全局环境引擎字段：
  - `profile`
  - `scenario`
  - `facts`
  - `constraints`
  - `glossary`
  - `simulation`
- 新增轻量记忆机制：
  - 节点输出自动写入 memory。
  - 后续节点自动注入最近 memory。
  - 支持 API 查询和清空。
- 新增轻量状态记忆 `simulation.state_memory`：
  - 从运行文本和节点结果自动提炼当前状态。
  - 当前状态包括 `task_state/collaboration_state/relationship_state`。
  - 变化记录保存在 `transitions`。
  - 后续节点通过 `_simulation` 注入状态记忆。
- `Simulation View` 展示：
  - progress
  - confidence
  - risk
  - alignment
  - Task/Collab/Relation 状态
- 新增轻量 `Relationship Map`：
  - 从当前 edges 显示 `source -> target`。
  - 展示互动模式、关系和强度。
  - 先做前端只读映射，不引入复杂关系数据库。

### Runtime, Queue, And Observability

- 运行系统支持队列：
  - Run queue
  - worker count
  - active/queued/completed/failed
  - avg wait
- 新增性能观测：
  - HTTP 请求统计。
  - P95 延迟。
  - 错误率。
  - 慢请求。
  - 运行失败数。
  - 慢节点数。
- 前端新增 `Performance Health` 和 `Run Queue` 面板。
- 前端新增 `Live Monitor`，支持粒度：
  - `fine`
  - `balanced`
  - `coarse`
- 运行指标包括：
  - duration
  - token estimate
  - succeeded/failed/skipped nodes
  - interactions
  - director guidance effect
- 支持 A/B run compare。

### Human Intervention And Decision Cards

- 新增人工干预机制：
  - `human_checkpoint` 节点。
  - `waiting_human` 状态。
  - `human_resumed` 事件。
  - `POST /api/runs/{run_id}/human-response`。
- 前端人工干预区支持展示 checkpoint question 和 director preview。
- 人工干预从自由文本升级为“决策卡按钮 + 可选补充文本”：
  - Continue
  - Adjust Direction
  - Reduce Cost
  - Inject Event
- 决策提交时写入 `metadata.action`，后续可用于更细的导演策略。

### Broadcast And Replay Experience

- `Live Glimpse` 升级为 `Live Broadcast`。
- 广播语气支持：
  - `newsroom`
  - `warroom`
  - `calm narrator`
- 广播内容从事件片段转成更像“现场播报”的句式。
- 前端保留最近广播流，用于增强过程可读性和趣味性。
- Timeline 支持滑动回放。
- Event Logs 支持逐条 Trace。

### Audit, Traceability, And Safety

- 新增运行审计与追溯能力：
  - 事件负载包含 `_trace`。
  - `_trace` 包括 `trace_id/parent_event_ids/caused_by/context_snapshot/prev_hash/event_hash`。
  - `GET /api/runs/{run_id}/trace/{seq}` 查询单事件因果上下文。
  - `GET /api/runs/{run_id}/audit/verify` 校验审计链完整性。
- 前端新增 `Trace Inspector`：
  - 查看父事件链。
  - 查看触发来源。
  - 查看上下文快照。
  - 手动校验审计链。
- 敏感字段自动脱敏：
  - `api_key`
  - `token`
  - `authorization`
  - `password`
  - `secret`
- 不做内容分级机制。

### External Agent Protocol

- 新增外接用户 agent 的文本协议思路和实现基础。
- 支持 `external_agent` 节点。
- 支持两种接入方式：
  - 用户 agent 自带本地 HTTP API。
  - 用户写 adapter/bridge 层。
- 新增协议说明：
  - `GET /api/protocols/text-agent-v1`
  - 文档：`docs/PROTOCOL_EXTERNAL_AGENT_V1.md`
- 外接 agent 输入包含：
  - environment
  - global_guidance
  - interactions
  - memory
  - simulation
  - constraints
- 当前主流程仍以文本协作为核心，多模态后续再做。

### Report Generation

- 新增报告生成能力：
  - `POST /api/runs/{run_id}/report`
- 支持两类报告模式：
  - `briefing`：正式汇报模式。
  - `story`：剧情/小说/剧本式总结。
- 前端支持设置：
  - mode
  - length
  - title
  - audience
  - style_prompt
- 报告可下载为 Markdown。
- 报告基于运行事件、metrics 和 evidence index 生成。

### Frontend Visual And Product Feel

- 前端视觉升级为模拟器舞台风格：
  - 深色画布。
  - 琥珀/青绿色重点色。
  - 扫描线背景。
  - 广播控制台。
  - 游戏化决策按钮。
  - 强化 React Flow 节点质感。
  - 强化角色悬浮卡质感。
- 保留三栏骨架：
  - 左侧配置与模板。
  - 中间画布。
  - 右侧观察、导演、干预、追溯与报告。
- 当前 UI 已具备“模拟器控制台”方向，但仍可继续优化为更强的游戏 HUD。

### Tests And Docs

- 新增 API E2E 测试。
- 新增 API surface 测试。
- 新增 human API 分支测试。
- 新增前端 Vitest 基础。
- 新增 store 测试。
- 新增 api client 测试。
- 后续注意：最近多次前端视觉和交互改动尚未完整跑 `npm test` / `npm run build`。
- 文档中心包括：
  - `docs/API_REFERENCE.md`
  - `docs/ARCHITECTURE.md`
  - `docs/DOCS_POLICY.md`
  - `docs/NODE_SPEC.md`
  - `docs/PRD_MVP.md`
  - `docs/PRODUCT_BRIEF.md`
  - `docs/PROTOCOL_EXTERNAL_AGENT_V1.md`
  - `docs/RUNBOOK_LOCAL.md`
  - `docs/TEST_PLAN.md`

### Known Product Gaps

- 角色视觉身份仍待设计：
  - avatar
  - sigil
  - color
  - portrait
- 关系网当前是轻量只读列表，还不是完整图谱。
- Simulation Seed 目前用于复现标识与模板保存，还未接入随机过程控制。
- 导演事件牌尚未实现。
- 高光回放尚未实现。
- 多模态协作暂未实现。
- 前端最新视觉改动仍需构建验证和交互走查。

### Frontend Localization

- Added lightweight frontend bilingual support with `zh/en` language state and a topbar language switch.
- Wired primary Web UI labels, controls, empty states, and major dashboard panel headings to the i18n dictionary.
- Auto Parse Plan now sends the selected UI language as `language` to the director compile API, so English users get English-oriented generated workflow text.
- Fixed frontend initialization order so broadcast rows no longer reference `uiMode` before it is initialized.

### Frontend Maintainability

- Split shared frontend app types into `frontend/src/appTypes.ts`.
- Moved bilingual UI dictionary into `frontend/src/i18n.ts`.
- Moved App-level support utilities, inspector panels, fallback node field schemas, simulation view helpers, monitor helpers, role-card parsing, relationship mapping, and broadcast formatting into `frontend/src/appSupport.tsx`.
- Extracted the top navigation/header into `frontend/src/components/Topbar.tsx`.
- Reduced `frontend/src/App.tsx` from a monolithic 2500+ line file to a smaller orchestration shell while preserving existing behavior.

- Added `docs/PRODUCT_HOMEPAGE_README.md` as the product homepage copy draft, preserving the user's core positioning while reducing overly explicit homepage wording risk.
- Updated the root `README.md` into a GitHub-style product homepage and synchronized `docs/PRODUCT_HOMEPAGE_README.md` with the same public-facing structure.
- Reworked `README.md` and `docs/PRODUCT_HOMEPAGE_README.md` to follow the user-provided homepage structure and logic, only softening explicit NSFW wording and examples.
- Replaced the previous bat/ps1 launcher with a single lightweight `run.py` launcher that only checks readiness and starts backend/frontend services.
- Fixed frontend build blockers after modularization and updated local run requirements to match Vite Node.js version expectations.
- Migrated frontend localization from an inline dictionary to runtime i18n with `i18next` and `react-i18next`, with locale resources split into `frontend/src/i18n/locales/zh-CN.json` and `en-US.json`. Run `npm install` in `frontend/` before the next build to install the new dependencies.

### Backend Framework Integration

- Refactored backend startup and shutdown from deprecated FastAPI `on_event` handlers to the framework-level `lifespan` hook.
- Consolidated run queue submission into `_enqueue_or_429`, so normal run, retry run, and human resume share the same queue-full behavior.
- Added `_mark_run_failed_queue_full` for newly created runs that cannot enter the queue, preserving traceable failed state instead of leaving stale pending records.
- Kept human intervention resume conservative: the original waiting run is only moved back to pending after the resume task is successfully queued.
- Integrated retry/resume patches into `WorkflowEngine` helpers for director preflight recognition, human resume events, retry context restoration, and restored-upstream routing.
- Verified backend and frontend after the refactor:
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 44 passed.
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> 24 passed.

### Full Log And Derived Outputs

- Added a source-of-truth Full Simulation Log layer for completed or in-progress runs.
- Added `GET /api/runs/{run_id}/full-log?format=markdown|json` with event count, node count, and full content.
- Full Log includes run metadata, node inputs/outputs/errors, event payloads, trace IDs, parent event references, and causality metadata.
- Report generation now explicitly derives from the Full Simulation Log and evidence lines.
- Added `narrative` as the product-facing literary/script output mode while keeping legacy `story` requests compatible.
- Frontend now has a Full Simulation Log panel with markdown/json viewing and download.
- Frontend report generator now separates `Briefing / Plain Report` from `Narrative / Literary Work`.
- Tests updated for full-log API/client coverage and narrative report flow.
- Verified:
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 44 passed.
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> 25 passed.

### Three-Layer Simulation Memory

- Reworked lightweight memory into three product-level layers: `fact`, `state`, and `character`.
- Kept the existing SQLite `memories` table and stored structured memory metadata in `tags_json` to avoid a heavy migration.
- Fact memory records what happened and links back to `source_event_seq` in the Full Log.
- State memory records current task/collaboration/relationship state snapshots with before/after transition metadata.
- Character memory records dynamic role evolution during simulation without overwriting the user's initial role card.
- Runtime memory injection now selects relevant memories for each node instead of blindly injecting the latest raw list.
- Memory prompt rendering now groups injected memory under Facts, Current State, and Character State.
- Memory list API now exposes `kind/scope/subject/importance/confidence/source_event_seq` for frontend display.
- Frontend memory panel now shows memory kind badges and source event references.
- Updated `docs/NODE_SPEC.md` and `docs/API_REFERENCE.md`.
- Verified:
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 45 passed.
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> 25 passed.

### Memory Recall And Write Triggers

- Added explicit memory write gating inspired by interaction-listener memory systems: nodes no longer blindly write every successful output.
- Added `_should_write_memory` to decide whether an interaction result is worth storing.
- Fact memory is written for meaningful interactions, decisions, conflicts, conclusions, high-importance outputs, or substantial content.
- State memory is written only when the simulation state changes.
- Character memory is written for agent/external-agent interactions or sufficiently important character evolution.
- Added `_memory_recall_score` so recall is ranked by scope, subject match, kind, importance, and source event linkage.
- Recall now happens immediately before node execution and injects only relevant three-layer memories into `_memory`.
- Updated `docs/NODE_SPEC.md` with write and recall trigger semantics.
- Added backend tests for write gating and three-layer recall injection.
- Verified:
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 46 passed.
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> 25 passed.

### Director Quality Correction

- Added low-cost director quality correction for simulation drift and flat outputs.
- Added `director_corrected` runtime event type.
- Added `director_quality_control` simulation policy parsing with `enabled`, `interval_nodes`, and `threshold`.
- Director quality checks run after non-director node success at a configurable interval, without extra LLM calls by default.
- Quality assessment detects low-substance output, missing concrete roleplay scene action, and missing research structure.
- Low-quality outputs produce corrective global guidance that is injected into downstream nodes through `_global_guidance`.
- Metrics now count director corrections, manual overrides, and vote finishes as director guidance interventions.
- Updated frontend event typing for `director_corrected`.
- Updated `docs/NODE_SPEC.md` with director quality correction semantics.
- Added backend test coverage for flat-output correction and downstream guidance injection.
- Verified:
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 47 passed.
  - `npm.cmd run build` -> passed after rerun; an initial Vite/Rollup Windows path emission error did not reproduce when `tsc` and `vite build` were run separately and then as the full npm script.
  - `npm.cmd run test:run` -> 25 passed.

### Frontend Product Maturity Pass

- Added a Studio Status Board under the mission strip without changing the existing visual direction.
- Status Board now surfaces the product artifact chain: Full Log as source-of-truth, with Briefing and Narrative as derived outputs.
- Added launch readiness summary for model configuration, entity/node count, interaction count, and detail granularity.
- Added director quality correction summary with latest guidance preview and correction count.
- Added compact three-layer memory counters for Fact, State, and Character memory.
- Updated Live Broadcast and Monitor formatting so `director_corrected` events are visible as high-priority director interventions.
- Adjusted canvas height and responsive layout to preserve the canvas-first Studio feel after adding the status board.
- Verified:
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> 25 passed.
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 47 passed.

### Frontend Status Board Copy Trim

- Removed explanatory product copy from the new Studio Status Board.
- Kept the board as a compact status HUD with short labels and counts only: Outputs, Setup, Director, Memory.
- Full Log, Briefing, and Narrative now show readiness state without explaining the feature model in the UI.
- Director quality area now shows correction count and latest event number instead of guidance prose.
- Verified:
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> 25 passed.

### Frontend Status Board Rollback

- Removed the recently added Studio Status Board visualization layer.
- Removed the Outputs, Setup, Director, and Memory summary cards from the main canvas shell.
- Removed the associated unused frontend state calculations and CSS rules.
- Restored the canvas-first layout height after removing the extra visual block.
- Preserved the underlying Full Log, Briefing, Narrative, Memory, and Director correction capabilities in their existing functional panels.
- Verified:
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> 25 passed.

### Frontend Product UI Cleanup

- Removed explanatory product copy from the normal UI surface.
- Kept functional labels, values, actions, compact empty states, and generated results.
- Removed entity/node card descriptions from the creation panel.
- Removed model setup helper copy and external-agent instructional copy from modal/panel surfaces.
- Removed brand subtitle text from the top navigation.
- Replaced example-heavy placeholders with short field placeholders.
- Made the Director mascot draggable within the canvas and persisted its position in local browser storage.
- Preserved click/keyboard access to the Director command bubble.
- Verified:
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> 25 passed.

### Frontend Copy Cleanup Follow-Up

- Removed remaining instructional paragraphs from inspector, edge, and environment panels.
- Replaced inspector panel header paragraphs with compact metadata rows.
- Shortened role-card, human-intervention, report, environment, and interaction placeholders.
- Shortened i18n empty states for role cards, broadcasts, memory, checkpoints, monitor data, relationships, and runtime events.
- Removed unused explanatory CSS selectors for brand subtitle, node-card descriptions, security notes, and panel-purpose text.
- Verified:
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> 25 passed.

### Director Plan Decomposition

- Expanded Auto Parse Plan Director prompts from simple workflow extraction to full simulation/research decomposition.
- Added structured extraction fields for assumptions, hypotheses, success metrics, decision criteria, research design, simulation design, output plan, and quality controls.
- Strengthened heuristic fallback so local/offline compile still produces differentiated defaults for market research, consulting, and story/roleplay simulation.
- Auto-generated agent nodes now receive `entity_type`, `responsibilities`, and prompt context for key questions, assumptions, decision criteria, and quality controls.
- `simulation_blueprint` now stores the complete decomposition for downstream Director, agent, report, and replay usage.
- Added `docs/PLAN_INPUT_GUIDE.md` and updated API docs for `/api/templates/compile`.
- Added API tests for enriched blueprint fields and auto roleplay inference.
- Verified:
  - `.venv\Scripts\python.exe -m pytest backend\tests\test_api_surface.py -q` -> 9 passed.
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 48 passed.

### Frontend Commercial Polish

- Fixed the Chinese inspector label from placeholder question marks to `编辑所选`.
- Added a final visual polish layer while preserving the yellow TV/game style.
- Tightened focus, active, disabled, hover, and keyboard-visible states for core buttons, cards, tabs, drawers, and form controls.
- Added themed scrollbars, React Flow control/minimap styling, stronger drawer depth, and compact empty-state treatment.
- Added reduced-motion handling for users who prefer minimal animation.
- Verified:
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> 25 passed.

### Frontend Flow Fixes

- Repaired the full Chinese locale file so runtime i18n no longer depends on mojibake labels.
- Reworked Studio Dock labels to use i18n keys instead of hardcoded bilingual strings.
- Added a run input field so workflow execution no longer sends the hardcoded `complete this task` payload.
- Added visible success/error notices for save, run, import, report, full-log, template, model, human-response, and Director command flows.
- Added import JSON validation before loading workflows into the canvas.
- Moved Full Simulation Log and Report Generator visibility from ops-only to report-capable modes, so roleplay mode can still produce logs and narrative/briefing outputs.
- Verified:
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> 25 passed.

### Preset Entity Node Visuals

- Repaired Chinese preset entity metadata for Individual, Group, Organization, Environment, Event, Artifact, and Human Gate.
- Added entity-specific React Flow node classes for newly created preset entity nodes.
- Restored entity-specific node styling when loading/importing existing workflows with `config.entity_type`.
- Added differentiated visual identities for preset entity nodes while leaving custom/developer nodes unchanged.
- Matched the entity library cards to the same visual language used on the canvas nodes.
- Verified:
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> 25 passed.

### Debug Checkpoints And Rollback

- Added rollback-capable run checkpoints from `succeeded`, `waiting_human`, and `failed` events.
- Added `GET /api/runs/{run_id}/checkpoints` for listing restartable state checkpoints with node, event, timestamp, and summary.
- Added `POST /api/runs/{run_id}/rollback` to create a new queued run from a selected event checkpoint without mutating the original run.
- Updated the execution engine retry restore path so rollback only reuses outputs up to the selected event sequence, avoiding future-state leakage.
- Added frontend checkpoint list, rollback reason field, manual refresh, and `Restart Here` actions in the trace/debug panel.
- Updated API docs for checkpoint and rollback semantics.
- Added e2e coverage for run -> checkpoints -> rollback -> new run success.
- Verified:
  - `npm.cmd run build` -> passed.
  - `.venv\Scripts\python.exe -m pytest backend\tests\test_api_e2e.py -q` -> 4 passed.
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 48 passed.
  - `npm.cmd run test:run` -> 25 passed.

### Background Time Space State

- Audited background/environment state flow across frontend schema, backend execution context, node prompt rendering, trace snapshots, and simulation replay.
- Added explicit `time_context` and `spatial_context` fields to workflow environment state.
- Added runtime `simulation_state.world_state` with `current_time`, `current_location`, `temporal_scope`, `spatial_scope`, `conditions`, and `active_events`.
- Injected time/space/world state into node prompts through the environment and simulation renderers.
- Added world state to trace context snapshots and simulation timeline entries.
- Added frontend environment panel fields for Time and Space, plus Simulation View display for current time/location.
- Updated auto plan compilation so Director-generated workflows include world state scaffolding.
- Updated API docs and tests for environment and simulation world state.
- Verified:
  - `npm.cmd run build` -> passed.
  - `.venv\Scripts\python.exe -m pytest backend\tests\test_engine.py backend\tests\test_api_surface.py -q` -> 37 passed.
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 48 passed.
  - `npm.cmd run test:run` -> 25 passed.

### Frontend Settings Consolidation

- Removed duplicated Model/API setup entry points from the topbar and Studio Dock.
- Kept API setup as the prominent mission-card path instead of exposing it in every toolbar.
- Hid developer nodes, external-agent protocol details, mode tuning, seed, detail granularity, and preflight controls from the normal Create flow.
- Added consolidated Product Settings and Developer Settings sections inside Advanced Console.
- Preserved all existing advanced capabilities while making the default user path focus on API setup, node creation, and run observation.

### Director Pre-Run Arrangement

- Connected the draggable Director mascot to natural-language workflow generation before any run exists.
- Reused the existing `/api/templates/compile` flow so pre-run Director requests produce a reviewable draft instead of directly overwriting the canvas.
- Kept in-run Director intervention unchanged: once a run exists, the same bubble sends scoped Director commands to the active run.
- Updated the bubble placeholder and actions so the user path is now API setup -> click Director -> describe simulation -> review/apply draft.
- Verified:
  - `npm.cmd run build` -> passed.
  - `npm.cmd run test:run` -> blocked by Windows EPERM writing `frontend/node_modules/.vite-temp` inside sandbox; escalation request was rejected by the approval channel.

### Director Draft Preview And Node Attribute Fixes

- Expanded the Director-generated draft confirmation card so users can review goal, background, rationale, and every generated node before applying it to the canvas.
- Completed auto-generated agent node configs with `entity_name`, `entity_profile`, `behavior_prompt`, `execution_mode`, `model_connection_mode`, model override fields, and external-agent bridge defaults.
- Added lightweight backend entity behavior helpers so generated nodes carry the same minimal real-world entity contract used by manual preset nodes.
- Repaired frontend entity definition mojibake and the broken Human Gate default question string.
- Added API test assertions for complete generated agent attributes.
- Verified:
  - `npm.cmd run build` -> passed.
  - `.venv\Scripts\python.exe -m pytest backend\tests\test_api_surface.py -q` -> 9 passed.
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 48 passed.
  - `npm.cmd run test:run` -> blocked by Windows EPERM writing `frontend/node_modules/.vite-temp` inside sandbox.

### Provider Default Model Compatibility

- Fixed DeepSeek/OpenAI-compatible runtime failures caused by auto-generated nodes hardcoding `gpt-4o-mini` after the user configured a non-OpenAI base URL.
- Changed generated Director and Agent nodes to leave `model` empty so platform default model configuration is used.
- Updated frontend default node configs and backend node specs to avoid suggesting an OpenAI model inside platform-default nodes.
- Updated agent execution semantics so `model_connection_mode=platform_default` ignores stale node-level model values and uses the global provider default; node model is only honored for `custom_endpoint`.
- Improved provider HTTP errors to include status code, URL, effective model, and provider response detail.
- Added tests covering platform-default model routing and generated node model defaults.
- Verified:
  - `npm.cmd run build` -> passed.
  - `.venv\Scripts\python.exe -m pytest backend\tests\test_engine.py backend\tests\test_api_surface.py -q` -> 38 passed.
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 49 passed.
- Follow-up: Director nodes now also default to the global provider model, so already-applied drafts with stale `model: gpt-4o-mini` do not keep failing on DeepSeek unless explicitly switched to `custom_endpoint`.
- Re-verified:
  - `npm.cmd run build` -> passed.
  - `.venv\Scripts\python.exe -m pytest backend\tests\test_engine.py backend\tests\test_api_surface.py -q` -> 38 passed.

### Director Confirmation And Readable Live

- Reworked Director auto-planning around an explicit execution contract: mode, research submode, participants, output format, and deterministic item/utterance limits.
- Added deterministic intent fallback so dialogue, family, screenplay, and character scenarios do not silently fall into custom mode when an LLM returns a non-standard domain label.
- Added output-contract parsing for requests such as "ten utterances or fewer" and enforced the limit on the final synthesis node.
- Changed roleplay participants to contribute only their own next action or one to two utterances instead of each drafting the entire scene.
- Reduced generated Director GPRO candidates from three to one by default; advanced workflows can still opt into more candidates.
- Stopped forwarding provider raw response JSON through interaction messages, substantially reducing downstream prompt growth while preserving raw data in trace payloads.
- Filtered Live broadcast to character content and critical events only; queued/running/succeeded lifecycle noise and provider JSON are no longer presented as broadcasts.
- Simplified Create navigation to Director, Manual, and Templates; moved mode choice into the Director draft confirmation card.
- Fixed draft application so the Create drawer closes and transient draft state is cleared after confirmation.
- Removed the Live tone selector from the normal user surface.
- Split Live into a compact timed broadcast and a click-to-open full scene transcript.
- The compact window now rotates through readable happenings, while the full transcript shows all available character dialogue, actions, Director interventions, and human checkpoints in chronological order.
- Removed trace navigation and technical event labels from the canvas Live window so no raw payload or provider structure is exposed in the normal product experience.
- Included readable outputs from manually named and external Agent nodes in the full transcript, not only auto-generated `agent_*` nodes.
- Condensed multiline content into short single-line canvas broadcasts while preserving the full text in the scene transcript.
- Removed the compact Live history stack so the canvas window shows exactly one rotating broadcast at a time.
- Removed `summary_1` synthesis output from Live/transcript and separated it into an explicit run result.
- Added an automatic Simulation Complete result dialog with the final synthesis, full-scene access, and one-click formal briefing or narrative generation.
- Clarified product semantics: report generation is an optional post-run transformation based on the complete log, not something Director conditionally skips for short simulations.
- Verified after the completion/results pass:
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 51 passed.
  - `npm.cmd run build` -> passed.
  - Frontend Vitest rerun was blocked by the approval channel before process start; the preceding Live pass had 24 tests passing, and TypeScript production compilation remains clean.
- Verified:
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 51 passed.
  - `npm.cmd run test:run` -> 24 passed.
  - `npm.cmd run build` -> passed.

### Stable Live Broadcast And Readable Reports

- Replaced Live's score-and-drop selection with a persistent FIFO broadcast queue: one new readable event is shown about every six seconds, unseen events are retained, and old events no longer rotate indefinitely.
- Kept the compact Live window on the latest broadcast while preserving the complete readable interaction list behind click-to-open Full Live Scene.
- Removed automatic `director_corrected` guidance from Live and the user-facing transcript; internal quality-control guidance remains available through advanced trace and audit data.
- Kept explicit user Director overrides, votes, human checkpoints, failures, character actions, and dialogue in the readable process stream.
- Split Full Live Scene into `Actual Interactions` and `Final Scene`, preventing summary-generated dialogue from being misrepresented as role-to-role runtime interaction while still exposing the complete synthesized performance.
- Changed the completed canvas Live window to open the full scene directly; the separate completion dialog continues to provide final result and report actions.
- Added a report-source adapter that extracts readable role outputs and final synthesis while excluding provider `raw`, `choices`, event payload JSON, prompts, tokens, traces, and internal Director correction text.
- Made formal briefing sections mode-neutral enough for both research and roleplay, and instructed both report modes to preserve the simulation language.
- Fixed narrative default titles and fallback behavior for the normalized `narrative` mode, and prevented duplicate top-level Markdown headings.
- Made historical report generation tolerate a missing workflow record by falling back to readable node-run data.
- Added regression coverage for hidden backstage Director guidance and raw-free report prompts.
- Verified:
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 52 passed.
  - `npm.cmd run test:run` -> 25 passed.
  - `npm.cmd run build` -> passed.

### Continuous Role-State Simulation Runtime

- Added a decoupled continuous simulation scheduler for Director-generated roleplay and reality-evolution simulations; legacy/manual DAG workflows remain backward compatible unless `execution_model=continuous` is explicitly set.
- Defined the hidden node-turn protocol around three product concepts:
  - `context`: current shared simulation state, recalled memories, recent actions, round/action position, and restrained Director guidance.
  - `role`: node identity, entity type, profile, responsibilities, behavior rule, and latest character-state memory.
  - `output`: one concrete role-owned action plus an optional simulation-state patch.
- Changed continuous simulations from one execution per Agent to repeated role turns bounded by `horizon_rounds` and the output contract's item/action limit.
- Persisted every turn as its own NodeRun and queued/running/succeeded event, so Live, Full Scene, Trace, audit, replay, and reporting now observe actual multi-round actions rather than summary-generated pseudo-events.
- Added per-turn fact and character memory writes; subsequent turns receive updated role memory and shared state.
- Added periodic Director control that can continue or stop after the minimum action budget while only correcting pacing, length, tone, detail, and drift. Director control does not select actions for roles.
- Kept hard action and round limits authoritative to prevent premature Director termination and runaway simulations.
- Changed final synthesis to consume the actual action ledger and explicitly forbid inventing additional actions that did not occur.
- Updated generated roleplay Director guardrails to preserve actor autonomy instead of applying consulting/MECE instructions to story simulations.
- Added failure persistence for continuous turns so Provider errors cannot leave NodeRun records stuck in `running`.
- Added a real-flow regression test proving two roles produce six separately persisted actions with explicit `context`, `role`, and normalized `action` contracts before one final synthesis.
- Verified:
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 53 passed.
  - `npm.cmd run test:run` -> 25 passed.
  - `npm.cmd run build` -> passed.

### Local Research Evidence Packages

- Added a mandatory Evidence review step when Director selects Research mode or the user switches a draft to Research; users may upload evidence or explicitly continue with model priors.
- Added `POST /api/evidence/parse` using raw binary uploads, avoiding an additional multipart runtime dependency.
- Added local parsing for direct files and ZIP packages containing `.xlsx`, `.docx`, `.csv`, `.json`, `.txt`, and `.md`.
- Implemented Office Open XML parsing with the Python standard library; model APIs receive extracted readable material rather than binary Excel/Word files.
- Unsupported `.pdf`, legacy `.xls/.doc`, encrypted, malformed, and unreadable files are surfaced as skipped/errors instead of being silently ignored.
- Added archive safety controls: 25 MB upload limit, 100-file limit, 80 MB expanded-size limit, path traversal rejection, bounded rows/sheets, and bounded extracted text.
- Added an Evidence confirmation UI showing parsed, skipped, and failed files before the Director refreshes the draft.
- Added `evidence_pack` to Director plan compilation and persisted confirmed evidence under `environment.simulation.evidence` with `evidence_status=user_confirmed`.
- Injected confirmed evidence into both continuous `context.evidence` and normal Agent simulation prompts.
- Added an `Observed evidence supplied by the user` report-source section so formal reports can distinguish uploaded evidence from simulated interactions.
- Added local parser, API, and Director-compile tests covering mixed ZIP packages, CSV, JSON, DOCX, XLSX, unsupported formats, unsafe archive paths, and confirmed evidence persistence.
- Verified:
  - `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 57 passed.
  - `npm.cmd run build` -> passed.
  - Frontend Vitest did not start because the sandbox escalation review service disconnected; this was not a test assertion failure.

### Roleplay Director Entity Constraint

- Added one general Director decomposition instruction: roleplay and world-evolution participants must be entities inside the simulated world; production roles are allowed only when the user explicitly requests collaborative creation.
- Kept the rule prompt-only and domain-neutral. No character names, story genres, scenario keywords, or role-title filters were added to runtime logic.
- Added a regression test that protects the Director prompt constraint during future refactors.
- Also retained the domain-neutral output-unit fix: final `section/chapter` limits constrain synthesis only, while only `utterance/action/turn/line` units can cap continuous simulation turns.
- Verified: `.venv\Scripts\python.exe -m pytest backend\tests -q` -> 59 passed.
