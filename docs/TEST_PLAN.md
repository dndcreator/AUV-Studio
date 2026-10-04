# 测试计划与覆盖

最后更新：2026-09-30

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
