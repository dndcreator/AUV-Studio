# Gold Cases 与跨模块任务

更新：2026-10-29。`backend/evals/behavior_cases.json` v2包含7项独立能力各5例，以及6项系统任务，共41例。本轮增加19项能力案例和3项短系统任务，原有案例也补齐判据。17项Judge校准样本单独维护，不与能力案例混算覆盖。

## 每项五例的风险分布

| 能力 | 五个代表主题 |
| --- | --- |
| Planning | 西方结尾范围、模拟访谈边界、核对到提案的分工、权限要求冲突、受访者隐私 |
| Actor | 私有计划不公开、群体不是单人、自主有效贡献、合理等待、小样本研究不确定性 |
| Action Check | 跨年代接触、允许有归属的谎话、禁止代写同伴回复、不支持的伤害结果、可接受的请求 |
| Director | 新对白不是终局、微观有效进展、真实完成后停止、上限不等于完成、重复交涉的场景反馈 |
| State Coder | 持久承诺而非姿态、指控不是事实、瞬时变化可空批次、自身意图修订、请求不是物品转移 |
| Memory | 旧承诺影响决策、群体私有边界、有来源纠错、冲突传闻不足、公共规则与同伴秘密分离 |
| Narrative | 小说事实忠实、剧本可表演性、未回应结尾、文化/叔父关系漂移、研究模拟不是市场成功 |

每例有input、pass_conditions、fail_conditions、allowed_variants、contrast_example和origin。正向/负向/边界/历史类别均有覆盖；negative表示错误诱因，不表示希望模型输出错误。创作、角色行为及动态概念名不要求固定答案；合理等待、微小进展和不同但有依据的结局/建议均可接受。

所有Gold为pending_human，不冒称已经人工确认。审阅后逐例填写gold.review_status=approved及真实reviewed_by。即使Judge已校准，未确认Gold仍为provisional_gold，不自动正式通过。被测模块只获得input；Judge获得判据与允许变化，不获得对照失败示例作为参考答案。

## 三个新增短任务

| Case ID | 实际链路 | 完成与失败边界 |
| --- | --- | --- |
| system_guild_to_novel | 自然语言Planning → 角色/路由/Action Check → Director/State Coder → SQLite → 小说/Fidelity | 实际选择或接取公会任务后成稿；小说不能补造未发生的接受、奖励或后续冒险 |
| system_research_to_brief | 自然语言Planning → 研究员/群体/审阅者自主协作 → 状态/完成审计 → 正式简报 | 六条虚构摘要得出有边界建议；不宣称真实总体比例、销量或已完成后续验证 |
| system_handoff_to_brief | 固定单向拓扑 → Mina/Ivo/Sen信息纠错 → 记忆/状态/完成审计 → 简报 | 实际传递后才能使用新信息，不心灵感应、不泄露凭证、不编造已发货 |

保留system_ending、system_research和system_long_continuity，覆盖历史结尾失败、研究任务及至少20轮实际执行覆盖。所有任务调用产品真实执行器和报告服务；离线假模型只证明接线与异常路径，不证明实际生成质量。

未完成模拟跳过成稿；报告失败保留已接受行动和审计，停止后续样本且不额外调用Judge。Planning、修复、运行、报告、Fidelity和Judge共用预算，不默认放宽调用次数或费用。

## 改善与退化

可比配置且至少两次完整试验下，保存每个case的结果、费用和波动。总分不变时也会显示某案例退步、另一案例改善，不被平均数掩盖。observed_direction只是描述性观察，非统计显著性；基准失配、覆盖缺失或单次结果不用于认证改善。被引用场景及记忆文件内容也计入case指纹，不能更换题目后继续比较旧分。

```powershell
cd backend
python -m app.behavior_eval --capability action_check --repeat 2 --judge --max-calls 30
python -m app.behavior_eval --case system_guild_to_novel --judge --max-calls 42 --max-total-tokens 100000 --max-cost-usd 0.10
```

以上仅预检，不读取模型配置/调用API。需要单独授权才加--execute。先审阅Gold，再按修改模块运行少量重复；关键版本才选短系统任务及长程任务，不默认付费跑全41例。Judge配置、校准及基线保存见 [TESTING.md](TESTING.md)。
