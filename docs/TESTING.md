# 测试与评测执行手册

更新：2026-10-29，北京时间。资产处置、风险矩阵见 [TEST_AUDIT.md](TEST_AUDIT.md)；本次比较记录见 `backend/evals/baselines/offline_v1.json`。

## 四层职责

| 层 | 验证什么 | 入口与频率 | 能证明的边界 |
| --- | --- | --- | --- |
| 单元 | 严格协议、类型、状态转换、权限、预算、评测器正反例 | `verify.py --layer unit`，高频 | 确定性工程规则，不是模型语义准确率 |
| 模块集成 | 实际引擎、SQLite、HTTP适配、状态传递、Worker、恢复和回滚 | `--layer integration`，日常/提交前 | 模型/供应商可替换；实际持久化及执行路径必须为真 |
| 智能行为 | Planning、Actor、Action Check、Director、State Coder、记忆、文学改写 | `app.behavior_eval --case ...`，改动对应模块且获授权后 | 独立Judge辅助判断；未经校准不认证质量 |
| 系统 | API生命周期至真实Worker的离线冒烟；以及研究、结尾、长程真实任务 | `--layer system`；真实部分使用`--capability system` | 后端任务端到端，不冒称浏览器交互或视觉验收 |

离线层使用同一份测试，仅通过显式资产/用例清单分组，不拷贝测试文件制造第二套回归。所有新文件必须在 `backend/tests/conftest.py` 归组，否则收集失败。不同层级验证同一高风险能力可以保留。

## 默认工程验收

在项目根目录、依赖已安装的Python环境中：

```powershell
python scripts/verify.py --layer all
python scripts/verify.py --layer unit
python scripts/verify.py --layer integration
python scripts/verify.py --layer system
python scripts/verify.py --layer frontend
```

Windows也可明确使用 `.venv\Scripts\python.exe`。默认 `all` 依次运行后端、Vitest和生产构建；不自动安装依赖、不读取模型配置、不调用任何真实模型评测。每个子进程默认180秒硬限额，超时终止自己启动的进程树，记录exit124；首个失败后停止后续步骤。只覆盖一个 `backend/data/verification.json`，记录命令、退出码和耗时。pytest另有60秒线程栈诊断；它不是单测强杀超时。

测试收集前隔离默认SQLite与媒体目录，不访问用户项目数据库。HTTPX真实传输默认全部禁止，包括可能转发付费请求的本地模型代理；MockTransport和ASGI TestClient不受影响。队列/恢复测试仍用真实队列和数据库。新增 `.github/workflows/test.yml` 自动执行同一入口，无模型密钥和付费评测步骤；尚未推送前不能声称GitHub CI已执行通过。

当前没有安装coverage/pytest-cov，因此不发布虚构的行/分支覆盖百分比。本轮以可执行用例、职责集和风险矩阵度量覆盖；今后如引入代码覆盖率，应另记录工具版本和真实报告，而非用测试条数推算。

## 行为案例与独立Judge

`backend/evals/behavior_cases.json` v1包含19项，覆盖8类职责。包括群体被写成单人、跨1500年握手、允许说谎、代写同伴回复、新对白冒充目标完成、瞬时细节污染状态、未经证实的盗窃、旧记忆纠错、秘密隔离、小说/剧本差异、未完成结尾和真实长程覆盖不足。原有固定文风/完整模拟案例继续保留。

```powershell
cd backend
python -m app.behavior_eval --list
python -m app.behavior_eval --case actor_secret --repeat 2 --judge --max-calls 8 --max-cost-usd 0.04
python -m app.behavior_eval --capability state_coder --repeat 2 --judge --max-calls 8 --max-cost-usd 0.05
```

以上仅预检，不加载密钥、不写结果。只有明确同意本次上限后才追加 `--execute`。执行必须显式选择case/capability；不会默认付费跑全套。普通模块只运行本次涉及的案例；关键版本再选择系统案例。长程案例至少观察20个实际引擎轮次才有长程覆盖；若提前结束，记为覆盖不足，不误判为遗忘，也不认证长程稳定。

Judge使用独立配置和独立请求上下文：

```powershell
$env:AUV_EVAL_JUDGE_BASE_URL = "http://127.0.0.1:1234/v1"
$env:AUV_EVAL_JUDGE_MODEL = "your-judge-model"
# 远程端点另外配置 AUV_EVAL_JUDGE_API_KEY；不从Actor或产品数据库复制Key。
```

兼容的本地免Key端点可不填Judge Key；可另设 `AUV_EVAL_JUDGE_PROVIDER`，默认OpenAI-compatible。可以使用同一模型的独立请求，但这不消除共同偏差；更换评审模型需要重新校准。Judge只存在于显式评测，不增加产品正常调用。

维度在 `evaluation/judging.py` 版本化：Planning看意图/角色/终点；Actor看所有权/知识/主动性；Checker看判决/依据；Director看目标进展/自主性/完成；Coder看持久性/来源/私有归属；记忆看连续性/私有边界；改写看来源/体裁风格；系统看实际完成/一致性/自主性/可读性。每维0缺失或矛盾、1严重失败、2混合或模糊、3基本符合、4充分符合，证据不足用null。任一0/1为fail，否则任一2/null为uncertain，否则pass。不补齐缺失维度、接受不存在的引用或相信Agent自评分；坏JSON、重复键、非有限值、截断和矛盾总判定不能通过。

系统Judge读取原始任务、角色、带ID的实际行动、终态和审计，不把Director的task_complete当证明；拒绝候选不是发生过的事实。状态编码结果及失败原因留在本地诊断，不复制为另一条事实时间线。最终快照与行动序列仍不能证明每个未保留的中间语义判断完全正确。

## 校准与人工标准

`backend/evals/judge_calibration.json`包含17个拟定标准样本，各职责均有pass/fail，另有uncertain。**它们目前是pending_human，不是已经审查过的人工金标准。** 用户需要审阅/修订标签，确认后填写 `review_status=approved` 和真实 `reviewed_by`。代码不会自动替用户批准。

```powershell
python -m app.behavior_eval --calibrate --capability memory --repeat 2 --max-calls 4
```

仍默认预检。授权执行后仅调用Judge，不调用Actor；不把label、样本ID或“应得分数”发给Judge。记录一致率、误接受率、误拒绝率、混淆矩阵及重复波动，缺少分母显示null。一个职责必须包含经审阅的正反例、当前Judge/标准指纹匹配、30天内记录、无执行缺失、一致率至少80%且误接受率为0，才标calibrated。小集合仅是校准冒烟，不是通用准确率保证。每个模式只保留一个latest结果；同一Judge/标准指纹下分职责校准保留其他职责的紧凑判定和原日期，不因校准另一个模块刷新旧结果的有效期。更换模型/标准后不混用旧记录，正式版本可显式保存版本化基线。

## 预算、重试与故障定位

Actor、修复、正常运行时Checker、State Coder与独立Judge共享一个物理尝试账本；切换Provider或输出上限不重置预算。调用、总Token、单次输出、估算USD和全流程异步期限同时限制；价格必须有限且非负，不能用NaN/Infinity绕过。CLI单次上限不超过400调用、100万Token、每次4000输出、5美元估算和1800秒；默认远小于这些上限。使用覆盖Actor和Judge两者的保守输入/输出单价；不是供应商账单硬保证。CJK估算不再简单除4。规划仍采用独立输出额度，计入同一总账。

遇到预算/超时/协议或网络错误后停止余下样本，记录not_run，保留分母；取消中的请求按保守预留用量记账。语义fail可继续已明确选择的其他样本，不自动修复被测答案去追求好分。每个样本记录case/trial/stage、结果、失败类型、调用区间和用量；每次请求仅留指纹、结束原因、延迟和计数，不复制原始提示词/Provider正文。系统失败另留检查结论、编码游标与拒绝码。只生成一个 `behavior_latest.json` 或 `calibration_latest.json`。

统一退出语义：0正式通过，1失败，2配置/预检错误，3待评审、证据不足或未校准。旧 `eval_runner` 也区分执行、规则与质量；无Judge/人工复核时不再把运行成功当质量通过。旧 `long_horizon_eval --execute` 是四个稀疏召回探针，不是60轮自主模拟；结构通过时语义仍待评审，退出3，关键词命中仅诊断。

## 回归与基线

```powershell
python -m app.behavior_eval --case actor_secret --repeat 2 --judge --max-calls 8 --baseline evals/baselines/actor-v1.json
# 授权后追加 --execute；保存新版本时另加 --promote-baseline actor-v2。
```

不存在/损坏的基线、已存在的晋级目标在模型调用前拒绝。晋级必须显式命名且repeat>=2，不覆盖旧文件；不允许执行错误/未执行样本晋级。可以保存失败或provisional参考基线，但校准状态随文件保留，不能冒充正式质量认证。基线只保存身份、模块汇总与成本，不复制作品正文。

案例内容、评审标准/版本/模型、被测模型、预算、重复次数和期限一致才比较；被测实现指纹单独记录，允许比较修改前后的代码而不把实现变动本身当作基准失配。至少两次完整试验才输出描述性分差，同时保留不确定率、错误率、波动案例、误判率和Token/费用。不同配置标incomparable，单次标insufficient_repeats，缺失试验标incomplete_coverage；不把5分差或单次好结果称作显著改善。

真实失败先脱敏为最小case，再经人工确认预期，进入普通回归或行为集；不要把失败输出改成期待答案使CI变绿。新增/删除用例要更新资产矩阵及changelog，说明其风险职责和取舍。

## 仍需验收

本轮不调用付费模型，不宣称模型质量、真实误判率或费用已改善。需要用户审阅校准标签并单独授权后，才能获得正式模型基线。浏览器Canvas拖拽、滚动、视觉与完整WebUI旅程仍未自动化；不能从组件回调或后端系统冒烟推断这部分通过。
