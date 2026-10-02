# 真实模型效果评测

最后更新：2026-10-02

## 目标

Eval 使用固定场景和当前已配置的模型，检查模拟效果是否随代码、提示词或模型调整而提升。它与 `pytest` 完全隔离，普通测试和启动 AUV 都不会触发付费调用。

## 默认场景

`roleplay_basic`：办公室上线冲突。

- 两个自治角色：产品设计师与产品经理。
- 固定事实、目标和知识边界。
- 最多三轮模拟、四个角色行动。
- Director 只审计连续性和结束条件。
- 检查角色参与、行动契约、状态推进、重复率，以及 Director 是否确认完成条件。

场景定义：`backend/evals/scenarios/roleplay_basic.json`。

## 预检

```powershell
cd backend
python -m app.eval_runner --scenario roleplay_basic
```

预检只展示预计调用规模和预算，不调用模型。

## 真实执行

```powershell
cd backend
python -m app.eval_runner --scenario roleplay_basic --execute
```

使用前端模型设置中已经保存的 Provider、Base URL、模型和 Key，也支持环境变量配置。运行结果写入本地 `backend/evals/results/`，该目录已加入 `.gitignore`。

需要额外的一次模型评审时：

```powershell
python -m app.eval_runner --scenario roleplay_basic --execute --judge
```

LLM Judge 仅作为辅助判断，不能替代规则检查和人工阅读。

World Book、节点私有信息和跨 60 轮依赖使用独立的按需评测，参见 `LONG_HORIZON_EVALUATION.md`。该评测不会随普通测试运行。

## 默认预算

- 最多 12 次模型调用。
- 最多 26,000 估算 Token。
- 每次最多 600 输出 Token。
- 估算费用上限 0.10 美元。
- 输入价格假设：1.00 美元/百万 Token。
- 输出价格假设：4.00 美元/百万 Token。
- Eval 禁用 Provider 自动重试。
- 官方 DeepSeek 端点在 Eval 中使用非思考模式，避免短结构化任务的推理 Token 吞掉输出预算；正常产品运行配置不受影响。

该默认档位已用内置 `roleplay_basic` 场景完成一次真实端点验证；不同模型的上下文计费和输出行为仍可能不同，执行前应先运行预检。

费用上限依赖用户提供的价格假设，不等同于供应商账单硬限额。调用次数、总 Token 和单次输出限制才是实际执行保护。应根据所用模型调整价格：

```powershell
python -m app.eval_runner --scenario roleplay_basic --execute `
  --input-usd-per-million 2.0 `
  --output-usd-per-million 8.0 `
  --max-cost-usd 0.30
```

CLI 还限制：最多 40 次调用、100,000 Token、单次 4,000 输出 Token和 5 美元估算预算，防止误输入极端参数。

## 副作用隔离

- 使用独立内存 SQLite，不写入用户工作流、运行历史和记忆。
- 禁止 Tool、External Agent 和 Human Checkpoint 节点。
- 场景最多 8 个节点、6 轮和 12 个角色行动。
- API Key 不写入评测结果。
- Provider URL 的用户信息、查询参数和片段不会写入评测结果。
- 完整日志只保存在本机、默认不进入 Git。

## 结果文件

每次执行生成：

- `*.json`：模型信息、预算、使用量、规则评分、可选 Judge 结果、与上次运行的差异和完整日志。
- `*.md`：便于人工阅读的结论、角色行动、检查清单和完整日志。

自动比较只把同场景的上一次结果作为参考，不会自动覆盖或认定“最佳基准”。规则分数变化至少 5 分才标记为提升或退化。

## 让我复核

执行后可以直接要求：“运行一次 `roleplay_basic` 效果测试并评估。”我会先进行预算预检，真实执行后阅读最新 JSON、Markdown 和完整日志，再判断：

- 角色是否一致、是否泄露私有信息。
- 节点是否真正自治。
- 剧情或任务是否发生状态推进。
- Director 是否越权。
- 最终输出是否忠于过程。
- 效果是否匹配成本和延迟。
- 相比上次是提升、持平还是退化。
