# 节点与编排语义

最后更新：2026-04-09

## 1. 节点类型

1. `prompt`
- 输入：模板变量
- 输出：渲染文本

2. `agent`
- 输入：prompt + 上下文
- 输出：模型响应
- 支持全局 provider 或节点级 custom endpoint

3. `tool`
- 输入：工具参数
- 输出：工具执行结果（白名单）

4. `condition`
- 输入：上下文
- 输出：布尔分支结果

5. `director`
- 输入：当前局势
- 输出：global_guidance
- 支持 GPRO-like 候选选优

6. `external_agent`
- 输入：文本任务与背景
- 输出：外接 agent 协作结果
- 协议：Text Agent Protocol v1.0

7. `human_checkpoint`
- 输入：问题模板/上下文提示
- 行为：触发人工干预暂停
- 输出：人工回复后继续下游

## 2. Edge Interaction

`edge.interaction` 字段：

- `mode`: report | instruction | feedback | dialogue | handoff
- `relation`: 角色关系（如 leader/member）
- `template`: 消息模板（可选）
- `required`: 是否要求上游输出存在
- `intensity`: 互动强度

## 3. 运行语义

1. 串行执行（按拓扑）
2. condition 节点控制边路由
3. 节点失败默认中断 run
4. 支持从失败节点重试
5. human_checkpoint 触发 `waiting_human`，人工回复后恢复执行

## 4. 轻量记忆机制

1. 记忆分为三层：`fact`、`state`、`character`
2. `fact` 记录发生过什么，来源绑定 `source_event_seq`
3. `state` 记录当前世界/任务/关系状态，并保留 `state_before/state_after`
4. `character` 记录角色在模拟中的动态变化，不覆盖用户初始角色卡
5. 下游节点输入自动注入筛选后的 `_memory`，不是全量日志
6. 记忆用于故事/角色模拟连续性，不替代 Full Log 回放

## 5. 记忆触发机制

1. 写入触发：节点完成一轮 interaction 后，由执行引擎判断是否值得写入
2. `fact` 写入条件：存在互动、重大决策/冲突/结论信号，或重要性达到阈值
3. `state` 写入条件：当前状态相对上一状态发生变化
4. `character` 写入条件：角色节点发生互动，或输出体现足够重要的角色变化
5. 召回触发：节点执行前按 subject、scope、kind、importance 和 source event 计算相关性
6. 召回优先级：global/environment > 当前角色自身 > state > 高重要性 fact > 相关 character

## 6. 导演质量纠偏

1. 导演质量纠偏不是内容分级，不判断题材是否允许
2. 默认低频检查非 director 节点输出，避免每步消耗额外模型调用
3. 检查维度包括输出实质、具体行动、冲突/进展、研究结构和互动强度
4. 低于阈值时写入 `director_corrected` 事件
5. 纠偏 guidance 注入后续节点的 `_global_guidance`
6. `roleplay` 模式强调具体场景、对白、行动、情绪压力和关系变化
7. `research` 模式强调假设、证据、不确定性和决策含义
