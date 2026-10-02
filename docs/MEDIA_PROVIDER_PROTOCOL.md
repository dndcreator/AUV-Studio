# 媒体 Provider 协议

最后更新：2026-09-30

## 1. 运行链路

`报告/剧本/可读模拟输出 -> 视觉镜头规划 -> 图片或视频 Provider -> 媒体资产`

媒体生成不改变模拟状态，也不把二进制内容写入运行事件。任务状态和资产引用保存于 `media_generations`。

## 2. Provider 类型

### OpenAI-compatible Image

- `provider`: `openai_compatible`
- 默认路径：`POST /images/generations`
- 请求字段：`model/prompt/n/size/quality`
- 接受响应：`data[0].b64_json` 或 `data[0].url`

Base64 图片最大 30 MB，写入 `AUV_MEDIA_OUTPUT_DIR`，默认 `./data/media`。

### OpenAI Video

- `provider`: `openai_video`
- 创建：`POST /videos`
- 状态：`GET /videos/{video_id}`
- 内容：`GET /videos/{video_id}/content`

创建请求为 multipart，包含 `model/prompt/seconds/size`。AUV 将 Provider 状态归一为 `queued/in_progress/completed/failed`。

### Generic HTTP Adapter

用于本地生成框架、ComfyUI 工作流桥接器或其他供应商。AUV 调用配置的生成路径：

```json
{
  "media_type": "image",
  "model": "workflow-a",
  "prompt": "model-ready visual prompt",
  "options": {
    "size": "1024x1024",
    "quality": "standard",
    "seconds": 4
  }
}
```

同步响应：

```json
{
  "status": "completed",
  "assets": [
    {"url": "http://127.0.0.1/output.png", "mime_type": "image/png"}
  ]
}
```

异步响应：

```json
{
  "status": "queued",
  "job_id": "job_123"
}
```

异步 Adapter 需要配置 `status_path`，例如 `/jobs/{job_id}`。状态响应使用与同步响应相同的字段。

资产支持字段：`url/data_url/mime_type/status/error`。若 Adapter 返回多个资产，AUV 当前读取每次镜头请求的第一个资产。

## 3. 密钥

- 前端提交的媒体 API Key 只保存在当前后端进程内，重启后清除。
- 长期配置使用 `AUV_IMAGE_API_KEY` 或 `AUV_VIDEO_API_KEY`。
- SQLite 只保存 Provider、Base URL、模型和路径，不保存媒体密钥。

## 4. 前端入口

模拟完成后在 Results 中选择图片或视频，设置风格、尺寸、数量或时长并生成。Provider 细节位于“媒体模型”弹窗。

普通用户使用 OpenAI 时只需选择服务并填写 Key。Base URL、模型和接口路径使用预设；路径配置默认折叠在高级设置中。操作步骤见 [图片与视频模型接入指南](./MEDIA_SETUP_GUIDE.md)。
