import type { ModelProviderKind } from "./types";

export const modelProviderPresets: Record<ModelProviderKind, { label: string; baseUrl: string; model: string }> = {
  openai_compatible: { label: "OpenAI Compatible", baseUrl: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  ollama: { label: "Ollama", baseUrl: "http://localhost:11434", model: "llama3.2" },
  llama_cpp: { label: "llama.cpp Server", baseUrl: "http://localhost:8080/v1", model: "local-model" },
  lm_studio: { label: "LM Studio", baseUrl: "http://localhost:1234/v1", model: "local-model" },
  vllm: { label: "vLLM", baseUrl: "http://localhost:8000/v1", model: "local-model" },
  localai: { label: "LocalAI", baseUrl: "http://localhost:8080/v1", model: "local-model" },
  tgi: { label: "Hugging Face TGI", baseUrl: "http://localhost:8080/v1", model: "tgi" },
  text_generation_webui: { label: "text-generation-webui", baseUrl: "http://localhost:5000/v1", model: "local-model" }
};

export function normalizeModelProvider(value: string): ModelProviderKind {
  return value in modelProviderPresets ? value as ModelProviderKind : "openai_compatible";
}

export function isKeylessLocalModel(provider: ModelProviderKind, baseUrl: string): boolean {
  if (provider !== "openai_compatible") return true;
  try {
    const host = new URL(baseUrl).hostname.toLowerCase();
    if (host === "localhost" || host === "127.0.0.1" || host === "::1" || host.endsWith(".local")) return true;
    const parts = host.split(".").map(Number);
    return parts.length === 4 && (
      parts[0] === 10
      || (parts[0] === 192 && parts[1] === 168)
      || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31)
    );
  } catch {
    return false;
  }
}
