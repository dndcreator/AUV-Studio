# AUV

> AUV，一个人工智能驱动的万能模拟引擎。  
> 在 AI 替代人类之前，先为你省去和人类打交道的时间。

哎呦喂，您上眼这口，那叫一个美，那叫一个地道。

AUV 是一个由人工智能驱动的万能模拟引擎，兼容调研、政治模拟、角色扮演、写作等多种场景。它可以通过调整前端，在你的电脑里模拟出一个真实的虚拟环境，根据导演和背景设定进行自我演化，并自动形成调研报告、剧情等成果。

是的，这个系统保留足够高的自由度。你可以在自己的本地模拟空间里定义角色、关系、边界和剧情方向。

## 主要特点

### 1. 可视化、操作简单的前端界面

拖动鼠标连接节点，就可以进行人物关系和逻辑线路的设置。

AUV 的前端会把角色、节点、关系、执行状态和结果展示出来。你不需要一开始就写复杂代码，也不需要理解一整套 agent 框架术语，只需要在可视化界面里把你想要的模拟结构搭出来。

### 2. 自由、可控的角色定义

想要一名数据分析师？一位罗马暴君？一个小说角色？一支竞选团队？一群目标用户？还是某种完全由你定义的人格？

只需要在前端定义节点的角色和设定，你就可以创造出无限的角色。角色可以有目标、性格、边界、行为风格，也可以根据不同场景承担不同职责。它们可以汇报、协作、服从、冲突、反驳、总结，也可以在导演和背景设定下按照自己的角色逻辑继续演化。

背景书允许你补充世界规则、事实、传言、研究假设和角色私有知识。普通用户只需要填写内容和“谁知道”，AUV 会在合适的场景中为不同节点装配各自可见的背景。

### 3. 直观、实时的信息播报

AUV 有着实时更新的事件广播，就像玩模拟游戏一样，重要和有趣的事件信息会自动推送给你。

你不必等到最后才看到结果。模拟过程中，节点的关键行为、关系变化、重要判断、异常事件和需要关注的片段，都会以更直观的方式展示出来。你可以像看直播、看战情室、看游戏事件日志一样观察整个模拟过程。

### 4. 高度可控、可干涉的模拟过程

想要让某些角色关系更亲密？想要让选举模拟多一些不可控因素？想要改变调研对象、组织关系、剧情方向或关键变量？

你可以随时更改模拟过程的各种设定，并对关键方向进行抉择。AUV 不要求你只能旁观，也不把模拟过程做成黑箱。你可以让它自动演化，也可以在关键节点介入，让模拟朝新的方向继续发展。

### 5. 聪明、善解人意的 AI 导演

不想自己做以上这些事情？没问题。

AUV 的 AI 导演会帮你完成大量前期工作。你只需要在对话里告诉它制片人的需求，它就可以帮助你拆解场景、生成角色、规划关系、组织流程、控制方向，并在模拟过程中进行纠偏。

导演不是为了替代你的控制权，而是为了降低配置成本。你可以让导演自动完成，也可以随时修改、覆盖或接管导演的判断。

### 6. 从模拟成果生成图片与视频

模拟结束后，可以把报告、剧情或剧本直接交给图片或视频模型。AUV 会先拆分视觉镜头，再连接 OpenAI-compatible 图片接口、OpenAI 视频接口或本地 Generic HTTP Adapter，并在 Results 中展示生成状态和资产。

## 模板与 Seed

你的模拟不必只留给自己。

如果对这次模拟非常满意，AUV 支持导出模板或者 Seed。其他用户可以一键获得你的所有模拟设定，包括角色、背景、关系、规则和流程配置，看看他们的模拟结果会如何。

一个好玩的设定、一个有价值的调研方案、一个复杂的政治推演、一个剧情世界观，都可以被保存、复现、分享和再次修改。

## 一键运行

准备好后端 Python 依赖和前端 npm 依赖后，在项目根目录运行：

```powershell
python run.py
```

这个脚本只是“开始游戏”的入口，不会创建虚拟环境，也不会安装依赖。它只会做基础检测，然后分别启动：

- 后端：`http://localhost:8000`
- 前端：`http://localhost:5173`

## 手动运行

### 后端

```powershell
cd backend
python -m venv .venv
. .venv/Scripts/Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 前端

```powershell
cd frontend
npm install
npm run dev
```

打开：

```text
http://localhost:5173
```

### 可选模型配置

不配置模型 key 时，AUV 可以用 mock response 跑通流程。  
如果你想进行真实模拟，可以设置 OpenAI-compatible 接口：

```powershell
$env:AUV_OPENAI_API_KEY="your_api_key"
$env:AUV_OPENAI_BASE_URL="https://api.openai.com/v1"
$env:AUV_OPENAI_MODEL="gpt-4o-mini"
```

图片与视频模型可在 Results 的“媒体模型”中配置。需要长期保留密钥时使用：

```powershell
$env:AUV_IMAGE_API_KEY="your_image_key"
$env:AUV_VIDEO_API_KEY="your_video_key"
```

也可以直接在前端模型向导选择本地模型：

- `Ollama`：原生支持 `http://localhost:11434/api/chat`，无需 API Key。
- `llama.cpp Server`、LM Studio、vLLM、LocalAI、Hugging Face TGI、text-generation-webui：提供独立预设并通过本地 OpenAI-compatible 协议连接。
- 自定义 OpenAI-compatible：支持其他 `/v1/chat/completions` 服务；回环和私有局域网地址无需 API Key。

## 文档

- [产品主页文案](./docs/PRODUCT_HOMEPAGE_README.md)
- [产品概览](./docs/PRODUCT_BRIEF.md)
- [本地运行手册](./docs/RUNBOOK_LOCAL.md)
- [API Reference](./docs/API_REFERENCE.md)
- [节点与编排语义](./docs/NODE_SPEC.md)
- [外接 Agent 文本协议](./docs/PROTOCOL_EXTERNAL_AGENT_V1.md)
- [媒体 Provider 协议](./docs/MEDIA_PROVIDER_PROTOCOL.md)
- [图片与视频模型接入指南](./docs/MEDIA_SETUP_GUIDE.md)
- [真实模型效果评测](./docs/EVALUATION_GUIDE.md)
- [变更记录](./docs/CHANGELOG.md)
