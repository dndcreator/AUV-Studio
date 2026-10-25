# 真实模型效果评测

最后更新：2026-10-20

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

## Scene Contract 专项

- `scene_micro_progress`：慢节奏、高细节、微小推进的还书对话。验证角色自主性、Delta 与行动是否一致、选择性细节和自然收束。
- `scene_research_progress`：无外部数据的早期产品研究协作。验证独立分工、可检验假设、不确定性边界和完成后停止。
- 两者均检查每轮收到场景契约、输出包含可为空的 Delta，以及 Director 对正确的全局轮号完成审计。精确重复率只是一项协议检查，语义重复和文学质量需要人工复核。

```powershell
cd backend
python -m app.eval_runner --scenario scene_micro_progress --judge --max-calls 14 --max-total-tokens 48000 --max-output-tokens 900 --max-cost-usd 0.10
python -m app.eval_runner --scenario scene_micro_progress --execute --judge --max-calls 14 --max-total-tokens 48000 --max-output-tokens 900 --max-cost-usd 0.10
python -m app.eval_runner --scenario scene_research_progress --execute --judge --max-calls 10 --max-total-tokens 36000 --max-output-tokens 900 --max-cost-usd 0.08
```

2026-10-19 的实施与真实样本复核见 [Scene Contract 评测记录](./evaluations/2026-10-19-scene-control.md)。该历史记录使用旧版产物格式；当前评审只读取简洁的实际行动与结果文件，不再复制原始 Full Log。

## 固定文风基准

`writing_styles` 使用同一段固定的还书场景素材，分别生成小说和剧本。不重跑模拟，避免把剧情随机性误认为文风变化；两个生成调用后，可选一个调用一次评审两份成稿。

写作要求与评分标准保存在 `backend/evals/styles.json`，各自带版本：

- `novel`：第三人称限知、克制现实主义小说；检查稳定视角、自然段、对白与选择性心理描写、潜台词。
- `screenplay`：简洁写实剧本；检查场景标题、可拍摄动作、人物对白、可表演的节奏；不要求小说式心理描写。
- 共同三项：事实忠实度、可读性、无冗余。丰富细节可以得高分，短文本不会自动得高分。
- 每种体裁另有形式、文风两项；共五项各 0-4 分：缺失、严重不符、好坏参半、基本符合、充分符合。代码求和乘 5，分别得到 100 分制结果，不把两种体裁混成一个排名。
- 模型必须给出五项有效整数分数；缺项、重复或格式错误显示不可用，不补分或当作通过。

```powershell
cd backend
python -m app.eval_runner --scenario writing_styles --judge --max-calls 3 --max-output-tokens 900 --max-total-tokens 12000 --max-cost-usd 0.05
python -m app.eval_runner --scenario writing_styles --execute --judge --max-calls 3 --max-output-tokens 900 --max-total-tokens 12000 --max-cost-usd 0.05
```

只测试一种形式时添加 `--style novel` 或 `--style screenplay`。也可以在普通模拟评测中添加这些选项，先运行真实模拟，再生成指定形式；每种形式额外一次调用。用户在产品中仍可自由填写文风，不受此处基准限制。

固定素材、写作要求、评分标准和评审模型一致时，记录才显示上次同配置的分差；内容指纹和版本避免修改标准后仍比较旧分数。若修改评审提示词的评分含义，应提升 `PROMPT_VERSION`。更换模型同时更换了当前评审模型，不直接比较旧分差。

写作基准将流程完整性与文学质量分开记录，命令行只显示各形式质量分，不把生成成功显示为文学质量 100 分。质量看 `Review` 中每种形式的五项得分和最多两条问题。普通模拟的 `Rule score` 也不能代表文学质量。模型评分仍有波动，固定标准不是文学质量保证；显著变化需要人工读正文，必要时用户授权重复试验，而不是默认多跑付费调用。

## Dynamic State 专项

`dynamic_state_quality` 使用一段包含对白、动作和短暂心理细节的固定关系冲突，验证模型是否只保留会约束后续行动的事实。规则评分检查：

- 至少捕获一项持久事实，但活动概念不超过四项。
- 每个状态引用准确的节点、行动序号和事件序号。
- 私有状态只能由其 owner 的行动建立。
- 不把皱眉、叹气、目光、心跳、语气等瞬时叙事细节写入状态。

预检与真实执行：

```powershell
cd backend
python -m app.eval_runner --scenario dynamic_state_quality
python -m app.eval_runner --scenario dynamic_state_quality --execute --judge
```

该场景预计约 9 次产品调用；`--judge` 额外增加一次评审调用，仍受统一调用、Token 和估算费用上限约束。

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
- 评测结果只保存在本机、默认不进入 Git。

## 结果文件

普通 Eval 每个场景只维护一个 `backend/evals/results/{场景名}.md`，再次执行覆盖该场景的最新记录：

- 正文保存一次：固定素材与各形式成稿，或实际角色行动与结局。
- 附简短评语、失败项、调用次数和估算费用，不保存提示词、原始 Provider 数据或重复全量日志。
- 文件首行包含精简的比较元数据，供下次读取；不再另外生成 JSON 文件。旧版历史产物不自动删除。

上一次结果只是参考，不自动认定为最佳基准。写作基准只对匹配配置显示分差，不自动据此认定提升或退化。产品内的 Full Log、Trace 和 Replay 不受此次评测文件简化影响；长程专项评测仍遵循其独立文档。

## 让我复核

执行后可以直接要求：“运行一次 `roleplay_basic` 效果测试并评估。”我会先进行预算预检，真实执行后阅读该场景的一份最新 Markdown，再就其中可见的行为判断：

- 角色是否一致、是否泄露私有信息。
- 节点是否真正自治。
- 剧情或任务是否发生状态推进。
- Director 是否越权。
- 最终输出是否忠于过程。
- 效果是否匹配成本和延迟。
- 相比上次是提升、持平还是退化。
