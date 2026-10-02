# 图片与视频模型接入指南

最后更新：2026-09-30

## 最短流程

1. 完成一次模拟并打开 Results。
2. 选择“图片”或“视频”。
3. 点击“媒体模型”。
4. 选择服务，填写必要信息，点击“测试并保存”。
5. 设置视觉风格、数量或时长，点击“生成”。

OpenAI 场景默认只需填写 API Key。Base URL、模型和接口路径使用内置值，不需要手工配置。

## OpenAI 图片

- 服务：`OpenAI 图片`
- 必填：API Key
- 默认模型：`gpt-image-1`
- 默认接口：`https://api.openai.com/v1/images/generations`

需要更换模型或代理地址时，在“高级设置”中修改。

## OpenAI 视频

- 服务：`OpenAI 视频`
- 必填：API Key
- 默认模型：`sora-2`
- 默认接口：`https://api.openai.com/v1/videos`

视频为异步任务。提交后 AUV 自动刷新状态，不需要用户手动轮询。

## OpenAI-compatible 图片服务

- 服务：`兼容接口`
- 填写：Base URL、模型、API Key
- 默认生成路径：`/images/generations`

服务需要接受 OpenAI Images 风格请求，并返回以下任一形式：

```json
{"data":[{"url":"https://example.com/image.png"}]}
```

```json
{"data":[{"b64_json":"..."}]}
```

## 通用或本地生成器

- 云端自定义服务选择“通用接口”。
- ComfyUI、Stable Diffusion WebUI 或自建服务选择“本地生成器”。
- AUV 默认连接 `http://127.0.0.1:8188`。
- 本地框架需要一个符合 AUV Generic HTTP Adapter 协议的轻量桥接器。

同步生成响应示例：

```json
{
  "status": "completed",
  "assets": [{"url": "http://127.0.0.1:8188/output.png", "mime_type": "image/png"}]
}
```

异步生成响应示例：

```json
{"status":"queued","job_id":"job_123"}
```

默认状态路径为 `/jobs/{job_id}`。完整字段见 [媒体 Provider 协议](./MEDIA_PROVIDER_PROTOCOL.md)。

## 密钥保存

前端填写的媒体 Key 只在当前后端进程中有效，重启后清除。长期使用时设置：

```powershell
$env:AUV_IMAGE_API_KEY="your_image_key"
$env:AUV_VIDEO_API_KEY="your_video_key"
python run.py
```

SQLite 不保存媒体 API Key。
会话密钥与 Provider 和 Base URL 绑定；切换到其他端点后必须重新填写，AUV 不会把旧 Key 转发给新服务。

## 常见问题

### 提示 API Key missing

重新打开“媒体模型”填写 Key，或使用环境变量后重启 AUV。

### 测试通过但生成返回 400

检查模型名称、尺寸和服务是否真正支持对应的生成接口。代理服务的 Base URL 通常应包含 `/v1`。

### 本地生成器一直 queued

检查状态路径是否包含 `{job_id}`，并确认桥接器返回 `queued/in_progress/completed/failed` 中的状态。

### 图片生成完成但无法显示

远程 URL 必须能从浏览器访问。也可以让 Adapter 返回 `data_url`，或使用 AUV 本地图片存储路径。
