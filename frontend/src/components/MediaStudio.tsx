import { useEffect, useState } from "react";
import { api } from "../api";
import { inferMediaPreset, mediaPreset, mediaPresets, presetRequest, type MediaPresetId } from "../mediaProviders";
import type { MediaConfig, MediaConfigRequest, MediaGeneration, MediaType, ModelConfigTestResponse } from "../types";

type Props = {
  runId: string;
  sourceText: string;
  copy: (zh: string, en: string) => string;
};

function assetUrl(value: string): string {
  if (!value) return "";
  if (value.startsWith("/api/")) return `http://localhost:8000${value}`;
  return value;
}

export function MediaStudio({ runId, sourceText, copy }: Props) {
  const initialPreset = mediaPreset("image", "openai");
  const [mediaType, setMediaType] = useState<MediaType>("image");
  const [presetId, setPresetId] = useState<MediaPresetId>("openai");
  const [config, setConfig] = useState<MediaConfig | null>(null);
  const [draft, setDraft] = useState<MediaConfigRequest>(presetRequest("image", initialPreset));
  const [apiKey, setApiKey] = useState("");
  const [style, setStyle] = useState("");
  const [shotCount, setShotCount] = useState(1);
  const [size, setSize] = useState("1024x1024");
  const [seconds, setSeconds] = useState(4);
  const [generation, setGeneration] = useState<MediaGeneration | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [testResult, setTestResult] = useState<ModelConfigTestResponse | null>(null);

  useEffect(() => {
    let active = true;
    api.getMediaConfig(mediaType).then((value) => {
      if (!active) return;
      setConfig(value);
      setPresetId(inferMediaPreset(value));
      setDraft({
        media_type: mediaType,
        provider: value.provider,
        base_url: value.base_url,
        model: value.model,
        endpoint_path: value.endpoint_path,
        status_path: value.status_path
      });
      setTestResult(null);
      setError("");
    }).catch((reason) => active && setError(String(reason)));
    return () => { active = false; };
  }, [mediaType]);

  useEffect(() => {
    if (!generation || !["queued", "in_progress", "partial"].includes(generation.status)) return;
    const timer = window.setTimeout(() => {
      api.getMediaGeneration(generation.generation_id).then(setGeneration).catch((reason) => setError(String(reason)));
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [generation]);

  const changePreset = (id: MediaPresetId) => {
    const next = mediaPreset(mediaType, id);
    setPresetId(id);
    setDraft(presetRequest(mediaType, next));
    setApiKey("");
    setTestResult(null);
    setShowAdvanced(false);
  };

  const changeMediaType = (next: MediaType) => {
    setMediaType(next);
    setConfig(null);
    setGeneration(null);
    setSize(next === "image" ? "1024x1024" : "1280x720");
  };

  const connect = async () => {
    setBusy(true);
    setError("");
    setTestResult(null);
    const payload = { ...draft, media_type: mediaType, api_key: apiKey || undefined };
    try {
      const tested = await api.testMediaConfig(mediaType, payload);
      setTestResult(tested);
      if (!tested.ok) return;
      const value = await api.saveMediaConfig(mediaType, payload);
      setConfig(value);
      setApiKey("");
      setSetupOpen(false);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

  const generate = async () => {
    setBusy(true);
    setError("");
    try {
      setGeneration(await api.generateRunMedia(runId, {
        media_type: mediaType,
        source_text: sourceText,
        style_prompt: style,
        shot_count: mediaType === "image" ? shotCount : 1,
        size,
        seconds
      }));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

  const selectedPreset = mediaPreset(mediaType, presetId);
  const needsSetup = !config || (config.requires_api_key && !config.has_api_key);
  const custom = presetId !== "openai";

  return (
    <div className="media-studio">
      <div className="media-studio-head">
        <div className="media-type-switch" role="group">
          <button className={mediaType === "image" ? "active" : ""} onClick={() => changeMediaType("image")}>{copy("图片", "Images")}</button>
          <button className={mediaType === "video" ? "active" : ""} onClick={() => changeMediaType("video")}>{copy("视频", "Video")}</button>
        </div>
        <button className="btn" onClick={() => setSetupOpen(true)}>{copy("媒体模型", "Media Model")}</button>
      </div>
      <div className="media-controls">
        <input value={style} onChange={(event) => setStyle(event.target.value)} placeholder={copy("视觉风格", "Visual style")} />
        {mediaType === "image" ? (
          <select value={shotCount} onChange={(event) => setShotCount(Number(event.target.value))} aria-label={copy("镜头数", "Shot count")}>
            {[1, 2, 3, 4].map((count) => <option key={count} value={count}>{count} {copy("张", "shots")}</option>)}
          </select>
        ) : (
          <select value={seconds} onChange={(event) => setSeconds(Number(event.target.value))} aria-label={copy("时长", "Duration")}>
            {[4, 8, 12].map((value) => <option key={value} value={value}>{value}s</option>)}
          </select>
        )}
        <select value={size} onChange={(event) => setSize(event.target.value)} aria-label={copy("尺寸", "Size")}>
          {(mediaType === "image" ? ["1024x1024", "1536x1024", "1024x1536"] : ["1280x720", "720x1280"]).map((value) => <option key={value}>{value}</option>)}
        </select>
        <button
          className="btn primary"
          disabled={!runId || busy}
          onClick={() => needsSetup ? setSetupOpen(true) : generate()}
        >
          {busy ? copy("生成中...", "Generating...") : needsSetup ? copy("连接模型", "Connect Model") : copy("生成", "Generate")}
        </button>
      </div>
      <div className="media-config-line">
        <span>{config?.model ?? "-"}</span>
        <span className={`media-status ${generation?.status ?? "idle"}`}>{generation?.status ?? copy("待机", "idle")}</span>
      </div>
      {error ? <div className="media-error">{error}</div> : null}
      {generation?.assets.length ? (
        <div className="media-assets">
          {generation.assets.map((asset) => {
            const src = asset.data_url || assetUrl(asset.url);
            return (
              <article className="media-asset" key={asset.asset_id}>
                {asset.status === "completed" && src && asset.media_type === "image" ? <img src={src} alt={asset.prompt} /> : null}
                {asset.status === "completed" && src && asset.media_type === "video" ? <video src={src} controls /> : null}
                {asset.status !== "completed" ? <div className="media-asset-wait">{asset.status}</div> : null}
                <details><summary>{copy("镜头提示", "Shot Prompt")}</summary><p>{asset.prompt}</p></details>
              </article>
            );
          })}
        </div>
      ) : null}

      {setupOpen ? (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal-card media-config-card">
            <div className="modal-head">
              <h2>{copy("连接媒体模型", "Connect Media Model")}</h2>
              <button className="btn" onClick={() => setSetupOpen(false)}>{copy("关闭", "Close")}</button>
            </div>
            <div className="media-provider-picks">
              {mediaPresets(mediaType).map((item) => (
                <button key={item.id} className={presetId === item.id ? "active" : ""} onClick={() => changePreset(item.id)}>
                  {copy(item.labelZh, item.labelEn)}
                </button>
              ))}
            </div>
            <div className="config-grid media-simple-config">
              {custom ? <label>Base URL<input value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} /></label> : null}
              {custom ? <label>{copy("模型", "Model")}<input value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} /></label> : null}
              {!selectedPreset.local ? (
                <label className={custom ? "" : "full-row"}>API Key<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={config?.has_api_key ? config.masked_api_key : "API Key"} /></label>
              ) : null}
            </div>
            <button className="media-advanced-toggle" onClick={() => setShowAdvanced((value) => !value)}>
              {showAdvanced ? copy("收起高级设置", "Hide Advanced") : copy("高级设置", "Advanced")}
            </button>
            {showAdvanced ? (
              <div className="config-grid media-advanced-config">
                {!custom ? <label>Base URL<input value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} /></label> : null}
                {!custom ? <label>{copy("模型", "Model")}<input value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} /></label> : null}
                <label>{copy("生成路径", "Generate Path")}<input value={draft.endpoint_path} onChange={(event) => setDraft({ ...draft, endpoint_path: event.target.value })} /></label>
                {draft.provider === "generic_http" ? <label>{copy("状态路径", "Status Path")}<input value={draft.status_path} onChange={(event) => setDraft({ ...draft, status_path: event.target.value })} /></label> : null}
                {selectedPreset.local ? <label>API Key<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={config?.has_api_key ? config.masked_api_key : copy("可选", "Optional")} /></label> : null}
              </div>
            ) : null}
            {testResult ? <div className={testResult.ok ? "ok-text" : "bad-text"}>{testResult.message}</div> : null}
            <div className="modal-actions">
              <button className="btn primary" disabled={busy} onClick={connect}>
                {busy ? copy("连接中...", "Connecting...") : copy("测试并保存", "Test & Save")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
