import type { MediaConfig, MediaConfigRequest, MediaProvider, MediaType } from "./types";

export type MediaPresetId = "openai" | "compatible" | "local_adapter";

export type MediaPreset = {
  id: MediaPresetId;
  labelZh: string;
  labelEn: string;
  provider: MediaProvider;
  baseUrl: string;
  model: string;
  endpointPath: string;
  statusPath: string;
  local: boolean;
};

const presets: Record<MediaType, MediaPreset[]> = {
  image: [
    { id: "openai", labelZh: "OpenAI 图片", labelEn: "OpenAI Images", provider: "openai_compatible", baseUrl: "https://api.openai.com/v1", model: "gpt-image-1", endpointPath: "/images/generations", statusPath: "", local: false },
    { id: "compatible", labelZh: "兼容接口", labelEn: "Compatible API", provider: "openai_compatible", baseUrl: "https://api.example.com/v1", model: "image-model", endpointPath: "/images/generations", statusPath: "", local: false },
    { id: "local_adapter", labelZh: "本地生成器", labelEn: "Local Generator", provider: "generic_http", baseUrl: "http://127.0.0.1:8188", model: "default", endpointPath: "/generate", statusPath: "/jobs/{job_id}", local: true }
  ],
  video: [
    { id: "openai", labelZh: "OpenAI 视频", labelEn: "OpenAI Video", provider: "openai_video", baseUrl: "https://api.openai.com/v1", model: "sora-2", endpointPath: "/videos", statusPath: "", local: false },
    { id: "compatible", labelZh: "通用接口", labelEn: "Generic API", provider: "generic_http", baseUrl: "https://api.example.com/v1", model: "video-model", endpointPath: "/generate", statusPath: "/jobs/{job_id}", local: false },
    { id: "local_adapter", labelZh: "本地生成器", labelEn: "Local Generator", provider: "generic_http", baseUrl: "http://127.0.0.1:8188", model: "default", endpointPath: "/generate", statusPath: "/jobs/{job_id}", local: true }
  ]
};

export function mediaPresets(mediaType: MediaType): MediaPreset[] {
  return presets[mediaType];
}

export function mediaPreset(mediaType: MediaType, id: MediaPresetId): MediaPreset {
  return presets[mediaType].find((item) => item.id === id) ?? presets[mediaType][0];
}

export function inferMediaPreset(config: MediaConfig): MediaPresetId {
  if (config.provider !== "generic_http") return "openai";
  try {
    const host = new URL(config.base_url).hostname.toLowerCase();
    return host === "localhost" || host === "127.0.0.1" || host === "::1" ? "local_adapter" : "compatible";
  } catch {
    return "compatible";
  }
}

export function presetRequest(mediaType: MediaType, preset: MediaPreset): MediaConfigRequest {
  return {
    media_type: mediaType,
    provider: preset.provider,
    base_url: preset.baseUrl,
    model: preset.model,
    endpoint_path: preset.endpointPath,
    status_path: preset.statusPath
  };
}
