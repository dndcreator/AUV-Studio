# Plan Input Guide

AUV supports two plan input styles:

1. Free text: a short natural-language request.
2. Structured brief: a lightweight plan with explicit fields.

The UI should stay clean. This guide is the place for detailed input guidance.

## Free Text

Free text is enough for a first draft. The Director will infer mode, actors, environment, stages, assumptions, and output plan.

Examples:

```text
做一个关于巧克力味啤酒的市场调研，关注18-22岁大学生，输出接受度、阻力和下一步验证建议。
```

```text
模拟一个现代战争小队在城市战中的故事，重点是队员冲突、任务转折，最后写成剧本。
```

## Structured Brief

For more stable results, users can provide:

```text
目标：
场景：
模式：auto / roleplay / research / custom
研究子模式：simulation / research / consulting
参与者或样本：
环境背景：
约束：
关键问题：
希望观察的变化：
输出形式：
风格：
规模或预算：
```

Fields can be omitted. The Director treats missing fields as assumptions, not as blockers.

## Director Decomposition

When compiling a plan, the Director should produce:

- `goal`: actual objective behind the user request
- `domain`: market research, consulting, story simulation, or general simulation
- `audience`: target audience or output receiver
- `deliverable`: final result format
- `constraints`: explicit limits
- `key_questions`: questions the simulation should answer
- `assumptions`: inferred missing context
- `hypotheses`: claims to test through simulation
- `success_metrics`: how to judge result quality
- `decision_criteria`: how to decide keep/drop/to-validate
- `method_modules`: methods/workstreams to use
- `roles`: entity-aware nodes and responsibilities
- `research_design`: methodology, sample logic, variables, bias risks, validation plan
- `simulation_design`: initial state, entities, interaction rules, phases, stop conditions
- `output_plan`: full log, briefing sections, narrative options
- `quality_controls`: safeguards against drift and weak conclusions

## Mode Selection

The Director should infer:

- `roleplay`: story, character evolution, script, novel, scene simulation
- `research.simulation`: what would happen under a described setup
- `research.research`: simulated fieldwork, interview, survey, respondent behavior
- `research.consulting`: issue tree, workstreams, option screening, recommendation
- `custom`: user-defined workflows that do not fit the standard modes

## Product Boundary

AUV is an early-stage simulation and screening tool. It can help reject weak options, explore plausible dynamics, and generate structured next steps. It does not replace real data collection, legal/medical/financial advice, or final factual verification.
