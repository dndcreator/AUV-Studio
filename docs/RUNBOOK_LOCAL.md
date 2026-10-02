# 本地部署与运行手册

最后更新：2026-09-30

## 1. 环境要求

- Python 3.10+
- Node.js 20.19+ 或 22.12+
- npm 9+

## 2. 一键启动

先确保后端 Python 依赖和前端 npm 依赖已经安装好，然后在项目根目录运行：

```powershell
python run.py
```

`run.py` 只是“开始游戏”的启动入口，不会创建虚拟环境，也不会安装依赖。它会做基础检测，然后分别启动：

- 后端：`http://localhost:8000`
- 前端：`http://localhost:5173`

## 3. 后端手动启动

```powershell
cd backend
python -m venv .venv
. .venv/Scripts/Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## 4. 前端手动启动

```powershell
cd frontend
npm install
npm run dev
```

访问：`http://localhost:5173`

## 5. 关键环境变量

- `AUV_OPENAI_API_KEY`
- `AUV_OPENAI_BASE_URL`（默认 `https://api.openai.com/v1`）
- `AUV_OPENAI_MODEL`（默认 `gpt-4o-mini`）
- `AUV_RUN_WORKER_COUNT`
- `AUV_RUN_QUEUE_MAX_SIZE`
- `AUV_IMAGE_API_KEY`
- `AUV_VIDEO_API_KEY`
- `AUV_MEDIA_OUTPUT_DIR`（默认 `./data/media`）

## 6. 排障速查

1. Run 长时间 pending：检查 `/api/queue/status`
2. 响应慢：检查 `/api/observability`
3. 卡在 waiting_human：调用 `/api/runs/{run_id}/human-response`
4. 外接 agent 失败：核对 endpoint、鉴权和协议字段
5. 前端报 `TypeError: crypto.hash is not a function`：当前 Node.js 版本过低。升级到 Node.js 20.19+ 或 22.12+，然后重新执行 `cd frontend && npm install`。

### 本地模型

1. Ollama：先执行 `ollama pull llama3.2`，保持 Ollama 服务运行，在模型向导选择 `Ollama`、地址 `http://localhost:11434`、模型 `llama3.2`。
2. llama.cpp：以 server 模式启动后选择 `llama.cpp Server`，默认地址 `http://localhost:8080/v1`。
3. LM Studio、vLLM、LocalAI、TGI、text-generation-webui：启动各自的 OpenAI-compatible server，选择对应预设并填写实际模型名称。
4. 本机及私有局域网本地端点无需 API Key；点击“测试连接”成功后再保存为平台默认。

## 7. 轻量记忆运维

- 查询：`GET /api/workflows/{workflow_id}/memories?limit=100`
- 清空：`DELETE /api/workflows/{workflow_id}/memories`

## 8. 外接 Agent 两种接入

1. 直连接入：`external_agent.integration_mode=http`
2. 本地桥接：`external_agent.integration_mode=bridge_local`，桥接模板在 `examples/agent_bridge`

## 9. 图片与视频模型

1. OpenAI：在 Results 的“媒体模型”中选择 OpenAI，填写 Key，点击“测试并保存”。
2. OpenAI-compatible 图片：选择“兼容接口”，填写 Base URL、模型和 Key。
3. 自定义视频或本地生成器：选择“通用接口”或“本地生成器”，接入 Generic HTTP Adapter。
4. 接入细节和排障见 [图片与视频模型接入指南](./MEDIA_SETUP_GUIDE.md)。
