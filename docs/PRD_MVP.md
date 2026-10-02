# AUV MVP 需求说明（PRD）

最后更新：2026-04-09

## 1. 目标

在本地环境提供可视化 Agent 编排能力，实现“可搭建、可执行、可观测、可复盘、可复用”。

## 2. MVP 范围

1. 可视化编辑：节点拖拽、连线、参数配置、导入导出 JSON
2. 执行引擎：DAG 校验、按依赖执行、上下文传递、失败中断、节点重试
3. 节点类型：prompt/agent/tool/condition/director/external_agent/human_checkpoint
4. 运行观测：事件流、日志、指标、性能告警、队列状态
5. 报告生成：briefing/story 双模式
6. 模板系统：保存、加载、二次编辑

## 3. 关键产品机制

1. 导演机制：Director 节点统一纠偏与风格约束
2. GPRO-like：多候选指导语生成后评分选优
3. 全局环境引擎：profile/scenario/facts/constraints/glossary 注入全流程
4. 互动边语义：edge.interaction 支持 report/instruction/feedback/dialogue/handoff
5. 人工干预：human_checkpoint 触发 waiting_human，人工回复后恢复执行

## 4. 非目标

1. 多用户权限
2. 云端协作
3. 复杂并行图调度
4. 多模态消息链路（当前暂不作为主路径）

## 5. 验收口径

1. 10 分钟内可搭建并跑通 3 节点以上流程
2. 每次 run 可回放到节点级输入/输出/状态/耗时
3. 失败可按节点重试
4. 人工干预可暂停并恢复同一 run
5. 本地 README 可完成一键启动
