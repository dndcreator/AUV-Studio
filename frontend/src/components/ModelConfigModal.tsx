import { isKeylessLocalModel, modelProviderPresets, normalizeModelProvider } from "../modelProviders";
import type { ModelConfig, ModelConfigTestResponse, ModelProviderKind } from "../types";

type Props = {
  open: boolean;
  config: ModelConfig | null;
  provider: ModelProviderKind;
  baseUrl: string;
  defaultModel: string;
  apiKey: string;
  testResult: ModelConfigTestResponse | null;
  saving: boolean;
  testing: boolean;
  copy: (zh: string, en: string) => string;
  onClose: () => void;
  onProviderChange: (provider: ModelProviderKind, baseUrl: string, model: string) => void;
  onBaseUrlChange: (value: string) => void;
  onDefaultModelChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onSave: () => void;
  onTest: () => void;
};

export function ModelConfigModal(props: Props) {
  if (!props.open) return null;
  const preset = modelProviderPresets[props.provider];

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card model-config-card">
        <div className="modal-head">
          <h2>{props.copy("模型配置", "Model Setup")}</h2>
          <button className="btn" onClick={props.onClose}>{props.copy("关闭", "Close")}</button>
        </div>
        <div className="config-grid">
          <label>
            {props.copy("接口类型", "Provider")}
            <select
              value={props.provider}
              onChange={(event) => {
                const next = normalizeModelProvider(event.target.value);
                const nextPreset = modelProviderPresets[next];
                props.onProviderChange(next, nextPreset.baseUrl, nextPreset.model);
              }}
            >
              {(Object.entries(modelProviderPresets) as [ModelProviderKind, { label: string }][]).map(([value, item]) => (
                <option key={value} value={value}>{item.label}</option>
              ))}
            </select>
          </label>
          <label>
            Base URL
            <input value={props.baseUrl} onChange={(event) => props.onBaseUrlChange(event.target.value)} placeholder={preset.baseUrl} />
          </label>
          <label>
            {props.copy("默认模型", "Default Model")}
            <input value={props.defaultModel} onChange={(event) => props.onDefaultModelChange(event.target.value)} placeholder={preset.model} />
          </label>
          {props.provider !== "ollama" ? (
            <label className="full-row">
              API Key
              <input
                type="password"
                value={props.apiKey}
                onChange={(event) => props.onApiKeyChange(event.target.value)}
                placeholder={props.config?.has_api_key ? props.config.masked_api_key : "API Key"}
              />
            </label>
          ) : null}
        </div>
        <div className="config-status-row">
          <span>{props.copy("来源", "Source")}: {props.config?.source ?? "-"}</span>
          <span>
            {props.copy("认证", "Authentication")}: {isKeylessLocalModel(props.provider, props.baseUrl)
              ? props.copy("本地免 Key", "Local / keyless")
              : props.config?.has_api_key ? props.config.masked_api_key : props.copy("未配置", "missing")}
          </span>
          {props.testResult ? (
            <span className={props.testResult.ok ? "ok-text" : "bad-text"}>
              {props.testResult.ok ? "OK" : "FAILED"} · {props.testResult.message}
              {props.testResult.latency_ms != null ? ` · ${props.testResult.latency_ms}ms` : ""}
            </span>
          ) : null}
        </div>
        <div className="modal-actions">
          <button className="btn" onClick={props.onTest} disabled={props.testing}>
            {props.testing ? props.copy("测试中...", "Testing...") : props.copy("测试连接", "Test Connection")}
          </button>
          <button className="btn primary" onClick={props.onSave} disabled={props.saving}>
            {props.saving ? props.copy("保存中...", "Saving...") : props.copy("保存", "Save")}
          </button>
        </div>
      </div>
    </div>
  );
}
