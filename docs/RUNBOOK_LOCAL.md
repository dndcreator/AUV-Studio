# 本地部署与运行手册

最后更新：2026-10-22

## 1. 环境要求

- Python 3.10+
- 文件型 SQLite 数据库；单个后端进程（不要使用多个 Uvicorn workers 连接同一数据库）
- Node.js 20.19+（20.x）或 22.12+（不支持 21.x、22.0-22.11）
- npm 9+

## 2. 一键启动

先确保后端 Python 依赖和前端 npm 依赖已经安装好，然后在项目根目录运行：

```powershell
python run.py
```

`run.py` 只是“开始游戏”的启动入口，不会创建虚拟环境，也不会安装依赖。它会做基础检测，然后分别启动：

- 后端：`http://localhost:8000`
- 前端：`http://localhost:5173`

前端端口被占用时启动会失败，不会悄悄切到另一个端口。启动中途失败、子服务退出或 Ctrl+C 会清理已启动服务；Windows 下同时清理该服务的子进程。正常关闭会把未完成队列任务记录为失败并保留审计事件；强制结束进程或断电不保证执行这些清理动作。

异常退出后，下次启动在开始处理任务之前，将上次遗留的活动运行标为失败并写入 `RUNTIME_INTERRUPTED` 审计事件。不会自动重放模型、工具或外部 Agent 调用。已持久化的输出和检查点保留，可选择重试或从检查点回滚；等待人工确认的运行不受影响。未落盘的响应无法恢复。

数据库旁的 `.runtime.lock` 文件用于单实例保护；进程退出后锁由操作系统释放，文件保留是正常现象，不要为了第二次启动而删除它。若提示已有后端持有数据库，先停止原服务。该锁不替代数据库本身的持久化与备份。

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
- `AUV_REPORT_SOURCE_MAX_CHARS`（报告完整素材字符上限，默认 64000；不等同于 Token 数或账单上限）
- `AUV_ALLOWED_ORIGINS`（逗号分隔的完整浏览器来源，设置后替换默认值；不支持 `*`）
- `VITE_API_BASE_URL`（前端开发/构建时读取，默认 `http://localhost:8000/api`，修改后需重启 Vite 或重新构建）
- `AUV_IMAGE_API_KEY`
- `AUV_VIDEO_API_KEY`
- `AUV_MEDIA_OUTPUT_DIR`（默认 `./data/media`）

默认浏览器来源允许 localhost、127.0.0.1、[::1] 的 HTTP 5173/4173/8000 端口。使用其他域名、端口或 HTTPS 时，把实际前端来源加入 `AUV_ALLOWED_ORIGINS` 并重启后端。无 Origin 的本地 API/Agent 调用继续可用；Origin 检查不是登录认证，部署仍以本地单用户为边界，不要裸露到公网。

## 6. 排障速查

1. Run 长时间 pending：检查 `/api/queue/status`
2. 响应慢：检查 `/api/observability`
3. 卡在 waiting_human：调用 `/api/runs/{run_id}/human-response`
4. 外接 agent 失败：核对 endpoint、鉴权和协议字段
5. 前端报 `TypeError: crypto.hash is not a function`：当前 Node.js 版本过低。升级到 Node.js 20.19+ 或 22.12+，然后重新执行 `cd frontend && npm install`。
6. API 返回 403 `browser origin is not allowed`：检查浏览器实际来源与 `AUV_ALLOWED_ORIGINS`，不要通过设置通配符绕过。
7. 模型返回 400/401/403/404：修正参数、模型名称、Key 或路径后重试，这些永久错误不再自动重复请求。408/429/5xx 仍按 Provider 重试次数指数退避。
8. 报告接口返回 413：素材超过报告输入预算，尚未调用写作模型。导出 Full Log，或检查模型上下文与费用后增大 `AUV_REPORT_SOURCE_MAX_CHARS` 并重启；不会静默丢弃结尾。

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
