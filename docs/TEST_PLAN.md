# 测试计划与覆盖

最后更新：2026-10-25

## 1. 后端测试分层

1. 引擎层（`backend/tests/test_engine.py`）
- DAG 循环检测
- 条件路由
- 失败重试
- director 与环境注入
- external_agent mock
- human_checkpoint 暂停/恢复

2. API 分支层（`backend/tests/test_human_api.py`）
- 人工干预接口异常分支与队列行为

3. API 面测试（`backend/tests/test_api_surface.py`）
- workflow/template CRUD
- interaction-modes
- memory list/clear

4. API E2E（`backend/tests/test_api_e2e.py`）
- run -> events -> metrics -> report -> compare
- human checkpoint 全流程
- 失败后按节点 retry
- 健康/协议/队列/观测接口

5. 背景书（`backend/tests/test_context_book.py`）
- 旧工作流兼容
- 中文短语触发和单层关联
- 节点私有知识隔离
- Token 预算与优先级
- 真实引擎提示词注入和激活追踪

6. 媒体生成（`backend/tests/test_media.py`）
- 媒体 API Key 不写入 SQLite
- OpenAI-compatible 图片任务与授权头
- OpenAI 异步视频创建和完成刷新
- Generic HTTP Adapter 请求/响应契约
- 外接 Agent URL 与 `task_id` 关联校验

## 2. 前端测试

1. store（`frontend/src/store.test.ts`）
- human_checkpoint 默认配置
- environment 归一化
- interaction 持久化
- context_book、时间和空间字段持久化

2. API client（`frontend/src/api.test.ts`）
- human-response 请求
- memory 请求
- 错误处理
- run 请求默认载荷
- media config、生成和异步刷新请求

## 3. 执行命令

- 后端：`cd backend && pytest -q`
- 前端：`cd frontend && npm run test:run`

## 4. 质量门槛（建议）

1. 新增接口必须包含成功 + 失败分支测试
2. 新增节点至少覆盖引擎执行与 API 场景
3. 关键流程变更必须补 E2E
4. PR 合并前后端测试必须全部通过

## 5. 轻量记忆专项

1. 节点成功后写入 memory 记录
2. 下游节点输入包含 `_memory`
3. 记忆查询与清空 API 正常

## 6. 真实模型 Eval

- 固定场景：`backend/evals/scenarios/roleplay_basic.json`
- 默认仅预检：`cd backend && python -m app.eval_runner --scenario roleplay_basic`
- 显式真实执行：追加 `--execute`
- 可选一次 LLM Judge：追加 `--judge`
- 评测结果：`backend/evals/results/`，不进入 Git
- 普通 `pytest` 只测试评测器逻辑，绝不调用真实模型
- 详细说明：[真实模型效果评测](./EVALUATION_GUIDE.md)

## 7. 动态状态专项

1. 任意领域概念可由 State Coder运行时创建，无需预设字段
2. 缺少未来相关性依据的概念被拒绝
3. 每轮、全局和单实体概念预算生效
4. 私有概念只对所属节点可见，路由和公共事件不可见
5. Director阶段审计同一次调用返回并应用状态 Patch
6. `dynamic_state_updated` 事件包含版本、来源和接受/拒绝结果
7. 声明类型与实际值不匹配时拒绝写入
8. 新概念初值失败时原子撤销 Schema，不残留空概念
9. 私有 owner、visibility、scope 与 value type 不可越权修改
10. Schema/State 操作必须引用本轮准确 action/event
11. round-TTL 状态到期自动退休
12. 连续 Dynamic State 缺少 Director 时工作流校验失败
13. Dynamic State 检查点回滚后精确恢复私有概念、值与来源
14. `dynamic_state_quality` 真实模型场景检查持久事实遗漏、状态膨胀、瞬时心理细节和来源完整性
15. 废弃状态字段会被忽略且不触发迁移或回退读取，规范输出只包含 v2 字段
16. Memory API 与运行召回只接受 `fact|character`，当前状态只来自 Dynamic State
17. 前端从 `runtime_metrics.progress` 和 Dynamic State 构建监测视图

## 8. 模块边界回归

1. `execution/graph.py` 的循环校验、条件分支和拓扑执行通过引擎真实流程覆盖
2. `execution/memory.py` 的写入触发、角色隔离、回滚裁剪和召回筛选独立可测
3. 证据、全量实况和运行结果覆盖层可独立渲染并只通过回调改变上层状态
4. 事件脱敏、哈希链、人工恢复和历史输出恢复通过真实运行与 Trace/Audit 用例覆盖
5. 检查点恢复后 Dynamic State、行动游标和连续模拟序号精确还原
6. Director 质量纠偏与投票策略作为纯函数被执行流程调用，不依赖 Engine 私有状态
7. Live 广播 hook 在新运行时重置游标，并按可读事件顺序生成快照
8. 画布显示状态按事件序号而非数组顺序确定，只使用当前回放范围，重置后回到待机；编辑态节点不被投影字段污染
9. 实体节点保留连接点、名称和双语状态，自定义技术节点不套用实体图标
10. 顶部项目菜单的导入/导出回调、文件选择重置、点击外部和 Escape 关闭、启动/停止中的禁用行为可独立测试
11. 浏览器检查桌面与 320px/390px 窄屏，覆盖中英文、画布、创建抽屉和模型弹窗；关闭抽屉不可聚焦，减少动态效果设置生效

## 9. Scene Contract 回归

- `test_scene_control.py` 验证契约控件类型、微小变化、整轮停滞计数、错号/缺失判断、转场更新、空行动可读性与长度无关的质量评分。
- 真实引擎使用两个角色自报虚假 Delta，由 Director 否决；验证无新增调用、允许提前停止、审计链有效和 API 回滚精确恢复契约与停滞计数。
- `scene_micro_progress`、`scene_research_progress` 为显式运行的真实模型评测；普通 pytest 不调用模型。
- 辅助模型评审读取同一份精简的实际行动与结果记录，不另存或重复输入原始日志；规则检查仍使用完整运行数据与事件，评审意见须人工复核。

## 10. 固定文风基准

- `test_writing_evaluation.py` 使用假模型验证同一素材下小说/剧本的独立要求，以及五项固定维度的计分、缺失评分不通过、无额外真实调用。
- 验证两次执行只覆盖一份 Markdown，正文不重复保存；素材、写作要求、评分规则与版本变化会使基准失配，不显示跨基准分差。
- 验证 `writing_styles --judge` 预检为三次调用，无 `--execute` 不访问模型。
- 产品报告回归验证用户文风原样传递、不要求最低字数、文学提示词不混入原始 Provider 数据或运行指标。
- 真实文学质量需显式执行 `writing_styles --execute --judge` 后阅读正文；接口生成完整的 100 分仅是协议结果，不是文学质量分。

## 11. 可靠性与本地边界回归（2026-10-22）

- `test_launcher.py`：Node 支持范围、部分服务启动失败、服务提前退出和中断时的清理。
- `test_provider.py`：真实 HTTP 状态对象下永久错误不重试、408/429/5xx 有界指数退避。
- `test_queue_manager.py`：真实执行器成功/白名单工具失败、持久化状态统计、Worker 异常后继续工作、错误落库失败不丢 Worker，以及正常停机处理活动/排队任务和审计链。
- `test_api_surface.py`：本地 Origin 允许、恶意/null/仿冒 Origin 拒绝、无 Origin 客户端继续可用、轻量状态成功/404、声明及分块上传大小限制。
- `test_evidence.py`：DOCX/XLSX 内部容器的展开限额，避免只检查外层 ZIP。
- `useRunPolling.test.tsx`：分页补齐、每轮工作上限、序号去重、切换运行取消旧响应、慢请求不重叠、超时后恢复，以及停止后的末尾事件完整性。
- `useLiveBroadcast.test.tsx`、`api.test.ts`、`apiConfig.test.ts`：广播回放清空、空角色名回退、可读校验错误、AbortSignal 传递、可配置后端和媒体资产地址一致。
- 本轮验证：169 项后端测试、55 项前端测试、生产构建、Python 编译和空白检查。TypeScript 启用未使用局部变量/参数检查。所有测试离线，无真实模型开销；未做规模压测，不能由此保证文学质量或万节点吞吐。

## 12. 中断恢复与报告完整性（2026-10-22）

- `test_run_recovery.py`：同库第二个实例被拒绝、异常退出租约释放（真实子进程 `os._exit`）、启动先恢复后启动 Worker、重复启动不重复事件、保留终态/人工等待/已完成输出/检查点、状态与审计写入失败一起回滚。
- `test_report_material.py`：briefing/narrative/story 的 16,000 字符之后仍包含中段、结尾及完整用户证据；预算内一次写作调用，超预算零调用。
- `test_api_e2e.py`：报告超预算的 HTTP 413 分支，以及失败重试保留用户输入。
- 验证：179 项后端测试、55 项前端测试、生产构建、Python 编译和空白检查。全部离线；不代表长篇文学质量、外部副作用恰好一次或未落盘内容恢复能力。

## 13. 连续模拟的消息、认知与播报（2026-10-25）

- `test_perception.py`：公开字段白名单与未知字段拒绝、有向连线及条件投递、模式/关系/模板/事件来源、未连接节点隔离、本人认知召回、高相关度角色记忆不越权、私有字段不触发共享背景。
- 使用真实 WorkflowEngine 跑两轮有边/无边场景，检查节点持久化输入、system prompt 模板、本人历史、路由/审计提示和 hash 链；另以 HTTP Adapter 请求对象验证外接 Agent 请求不含其他节点私有认知。
- 允许连续通信环，仍拒绝 DAG 环。修改旧的他人角色记忆泄漏断言；长期评测采用生产 `action` 协议，原评分阈值保持不变。
- `useLiveBroadcast.test.tsx`、`livePlayback.test.ts`、`LiveControls.test.tsx`：最新事件即时同步、演播顺序及积压节奏、切换模式不重放旧队列、完整可读记录保留、切换模式不误打开日志、运行时中英文标签。
- 验证：187 项后端、59 项前端测试，生产构建、Python 编译及空白检查。无付费调用；不证明模型绝不在公开文本里泄露秘密，未引入单 run 并行，也未做规模压测或新浏览器截图验收。

## 14. 三项行为验收（2026-10-25）

- `test_behavior_validation.py`：秘密及反向来源泄漏、早于投递的知识、不完整传播不算通过、标识别名、自报 intent 不等于自主性、真实探针缺失不回退 simulated。
- 同文件使用真实 WorkflowEngine 跑 24 轮 / 72 个假模型行动，验证早期约束不丢失、私有概念不扩散、角色/相邻通道完整、无来源状态变化连续被拒绝、时间坐标稳定和审计链完整。
- `test_long_horizon_evaluation.py` 验证 Provider 失败仍保留错误、已尝试调用数和缺失探针；普通测试不访问网络模型。
- 显式真实验收的两次短场景有正反样本，详情见行为验收记录；最新职责与审计拓扑修正未重复付费验证，长期真实模型质量仍无通过结论。
- CLI 对 fail/inconclusive 返回非零，不因 run 已结束而把未覆盖传播视作通过；邻居元数据不自动共享真实角色标签。
- 最终回归：197 项后端、59 项前端测试、生产构建、Python 编译及空白检查。

## 15. 行为问题根因回归（2026-10-25）

- 24 轮假模型测试读取审计职责表和 `received_event_seqs`，验证角色归属与一跳投递证据。未知收件不当成显式空收件；不把图可达性当作已收件。
- router/agent/audit/budget 四种异常后，状态、已完成行动、场景控制、hash 链及可回滚检查点保持完整。
- ConnectTimeout/ReadTimeout/WriteTimeout/PoolTimeout/ConnectError 空消息给出安全诊断；retry_count=0 时只发一次请求。
- 满 24 个 observed rounds 的失败运行标为 incomplete，而非已完成长期验证；验收材料保留角色、收件引用和快照可用性。
- 验证：207 项后端、59 项前端测试、生产构建、Python 编译及空白检查。本轮零真实模型调用；语义改进仍待受控真实复测。
