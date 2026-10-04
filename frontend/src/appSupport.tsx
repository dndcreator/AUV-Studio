import type { Edge, Node } from "reactflow";
import { defaultEdgeInteraction } from "./store";
import type { NodeFieldSpec, NodeSpec, NodeType, RunEvent } from "./types";
import type { RoleCard, UiMode } from "./appTypes";
import { useTranslation } from "react-i18next";
import type { BackgroundEntry, ContextBook } from "./types";

export function statusClass(status: string): string {
  if (status === "queued" || status === "running" || status === "pending") {
    return "running";
  }
  if (status === "waiting_human") {
    return "waiting";
  }
  if (status === "succeeded") {
    return "succeeded";
  }
  if (status === "failed") {
    return "failed";
  }
  return "idle";
}

export function MetricCard(props: { label: string; value: string }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{props.label}</div>
      <div className="metric-value">{props.value}</div>
    </div>
  );
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) {
    return "-";
  }
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(2)}s`;
}

export function formatRatio(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "-";
  }
  const n = Math.max(0, Math.min(1, value));
  return `${Math.round(n * 100)}%`;
}

export function formatDynamicStateValue(value: unknown): string {
  if (value == null || value === "") {
    return "-";
  }
  if (Array.isArray(value)) {
    return value.map((item) => formatDynamicStateValue(item)).join(" · ");
  }
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}: ${formatDynamicStateValue(item)}`)
      .join(" · ");
  }
  return String(value);
}

export function buildSimulationView(events: RunEvent[]): {
  stepSeq: number | null;
  phaseName: string;
  variables: { progress: number; confidence: number; risk: number; alignment: number };
  worldState: { current_time: string; current_location: string; temporal_scope: string; spatial_scope: string };
  stateMemory: { task_state: string; collaboration_state: string; relationship_state: string };
  dynamicState: {
    enabled: boolean;
    version: number;
    schemaVersion: number;
    concepts: Array<{ id: string; description: string; value: unknown; confidence: number | null }>;
  };
  timeline: Array<{ seq: number; nodeId: string; status: string; phaseName: string }>;
} {
  const fallback = {
    stepSeq: null,
    phaseName: "",
    variables: { progress: 0, confidence: 0, risk: 0, alignment: 0 },
    worldState: { current_time: "", current_location: "", temporal_scope: "", spatial_scope: "" },
    stateMemory: { task_state: "planning", collaboration_state: "aligned", relationship_state: "neutral" },
    dynamicState: {
      enabled: false,
      version: 1,
      schemaVersion: 0,
      concepts: [] as Array<{ id: string; description: string; value: unknown; confidence: number | null }>
    },
    timeline: [] as Array<{ seq: number; nodeId: string; status: string; phaseName: string }>
  };
  const sorted = [...events].sort((a, b) => Number(a.seq ?? 0) - Number(b.seq ?? 0));
  const timeline: Array<{ seq: number; nodeId: string; status: string; phaseName: string }> = [];
  let lastVars = { progress: 0, confidence: 0, risk: 0, alignment: 0 };
  let lastWorldState = { current_time: "", current_location: "", temporal_scope: "", spatial_scope: "" };
  let lastStateMemory = { task_state: "planning", collaboration_state: "aligned", relationship_state: "neutral" };
  let lastDynamicState = fallback.dynamicState;
  let phaseName = "";
  let stepSeq: number | null = null;
  for (const e of sorted) {
    const payload = e.payload as Record<string, unknown>;
    const sim = payload.simulation_state as Record<string, unknown> | undefined;
    const dynamicState = (payload.dynamic_state ?? sim?.dynamic_state) as Record<string, unknown> | undefined;
    if (dynamicState && typeof dynamicState === "object") {
      const concepts = dynamicState.concepts as Record<string, Record<string, unknown>> | undefined;
      const values = dynamicState.values as Record<string, Record<string, unknown>> | undefined;
      lastDynamicState = {
        enabled: Boolean(dynamicState.enabled),
        version: Number(dynamicState.version ?? 1) || 1,
        schemaVersion: Number(dynamicState.schema_version ?? 0) || 0,
        concepts: concepts && typeof concepts === "object"
          ? Object.entries(concepts).map(([id, concept]) => ({
              id,
              description: String(concept.description ?? ""),
              value: values?.[id]?.value,
              confidence: typeof values?.[id]?.confidence === "number" ? Number(values[id].confidence) : null
            }))
          : []
      };
    }
    if (!sim || typeof sim !== "object") {
      continue;
    }
    const vars = sim.variables as Record<string, unknown> | undefined;
    if (vars && typeof vars === "object") {
      lastVars = {
        progress: Number(vars.progress ?? lastVars.progress) || 0,
        confidence: Number(vars.confidence ?? lastVars.confidence) || 0,
        risk: Number(vars.risk ?? lastVars.risk) || 0,
        alignment: Number(vars.alignment ?? lastVars.alignment) || 0
      };
    }
    const worldState = sim.world_state as Record<string, unknown> | undefined;
    if (worldState && typeof worldState === "object") {
      lastWorldState = {
        current_time: String(worldState.current_time ?? lastWorldState.current_time),
        current_location: String(worldState.current_location ?? lastWorldState.current_location),
        temporal_scope: String(worldState.temporal_scope ?? lastWorldState.temporal_scope),
        spatial_scope: String(worldState.spatial_scope ?? lastWorldState.spatial_scope)
      };
    }
    const stateMemory = sim.state_memory as Record<string, unknown> | undefined;
    const currentStates = stateMemory?.current_states as Record<string, unknown> | undefined;
    if (currentStates && typeof currentStates === "object") {
      lastStateMemory = {
        task_state: String(currentStates.task_state ?? lastStateMemory.task_state),
        collaboration_state: String(currentStates.collaboration_state ?? lastStateMemory.collaboration_state),
        relationship_state: String(currentStates.relationship_state ?? lastStateMemory.relationship_state)
      };
    }
    const phaseIndex = Number(sim.current_phase_index ?? -1);
    const phases = sim.phases as Array<Record<string, unknown>> | undefined;
    if (Array.isArray(phases) && phaseIndex >= 0 && phaseIndex < phases.length) {
      phaseName = String(phases[phaseIndex]?.name ?? phaseName);
    }
    timeline.push({
      seq: Number(e.seq ?? 0),
      nodeId: e.node_id,
      status: e.event,
      phaseName
    });
    stepSeq = Number(e.seq ?? 0);
  }
  if (timeline.length === 0) {
    return { ...fallback, dynamicState: lastDynamicState };
  }
  return {
    stepSeq,
    phaseName,
    variables: lastVars,
    worldState: lastWorldState,
    stateMemory: lastStateMemory,
    dynamicState: lastDynamicState,
    timeline
  };
}

export function buildMonitorRows(
  events: RunEvent[],
  mode: "fine" | "balanced" | "coarse",
  nodeFilter: string
): Array<{ seq?: number; nodeId: string; level: "info" | "warn" | "critical"; message: string; time: string }> {
  const rows: Array<{ seq?: number; nodeId: string; level: "info" | "warn" | "critical"; message: string; time: string }> = [];
  const filtered = nodeFilter === "all" ? events : events.filter((e) => e.node_id === nodeFilter);
  for (const e of filtered) {
    const level: "info" | "warn" | "critical" =
      e.event === "failed" ? "critical" : e.event === "running" || e.event === "waiting_human" || e.event === "director_corrected" ? "warn" : "info";
    if (mode === "balanced") {
      if (
        !(
          e.event === "running" ||
          e.event === "succeeded" ||
          e.event === "failed" ||
          e.event === "waiting_human" ||
          e.event === "director_overridden" ||
          e.event === "director_corrected" ||
          e.event === "dynamic_state_updated" ||
          e.event === "dynamic_state_failed"
        )
      ) {
        continue;
      }
    }
    if (mode === "coarse") {
      if (
        !(
          e.event === "succeeded" ||
          e.event === "failed" ||
          e.event === "waiting_human" ||
          e.event === "director_overridden" ||
          e.event === "director_corrected" ||
          e.event === "dynamic_state_updated" ||
          e.event === "dynamic_state_failed" ||
          e.node_id.startsWith("director")
        )
      ) {
        continue;
      }
    }
    rows.push({
      seq: e.seq,
      nodeId: e.node_id,
      level,
      message: monitorMessage(e),
      time: new Date(e.timestamp).toLocaleTimeString()
    });
  }
  const maxRows = mode === "fine" ? 200 : 120;
  return rows.slice(-maxRows).reverse();
}

export function buildMonitorStats(events: RunEvent[]): { running: number; succeeded: number; failed: number; waiting: number } {
  const latest = new Map<string, RunEvent["event"]>();
  for (const e of events) {
    latest.set(e.node_id, e.event);
  }
  let running = 0;
  let succeeded = 0;
  let failed = 0;
  let waiting = 0;
  for (const v of latest.values()) {
    if (v === "running" || v === "queued") {
      running += 1;
    } else if (v === "waiting_human") {
      waiting += 1;
    } else if (v === "succeeded") {
      succeeded += 1;
    } else if (v === "failed") {
      failed += 1;
    }
  }
  return { running, succeeded, failed, waiting };
}

export function monitorMessage(e: RunEvent): string {
  if (e.event === "failed") {
    return "Execution failed, manual check recommended.";
  }
  if (e.event === "running") {
    return "Node is actively processing.";
  }
  if (e.event === "succeeded") {
    return "Node finished successfully.";
  }
  if (e.event === "queued") {
    return "Node queued and waiting for prerequisites.";
  }
  if (e.event === "waiting_human") {
    return "Paused and waiting for human intervention.";
  }
  if (e.event === "human_resumed") {
    return "Human response accepted, run resumed.";
  }
  if (e.event === "director_overridden") {
    return "Director override applied to downstream execution.";
  }
  if (e.event === "nodes_activated") {
    return "AI routing selected the relevant autonomous nodes.";
  }
  if (e.event === "episode_audited") {
    return "Director audited the current episode boundary.";
  }
  if (e.event === "director_corrected") {
    const payload = e.payload ?? {};
    const score = typeof payload.score === "number" ? ` score=${payload.score.toFixed(2)}` : "";
    return `Director quality correction issued.${score}`;
  }
  if (e.event === "dynamic_state_updated") {
    const changed = Boolean(e.payload?.changed);
    const count = Number(e.payload?.active_concepts ?? 0) || 0;
    return changed ? `Canonical state updated. ${count} active concepts.` : "Canonical state reviewed; no durable change.";
  }
  if (e.event === "dynamic_state_failed") {
    return "Canonical state update failed; participant actions were preserved.";
  }
  return "Node skipped by routing/condition.";
}

export function EdgeInteractionPanel(props: { edge: Edge; modes: string[]; onChange: (key: string, value: string | number | boolean) => void }) {
  const { i18n } = useTranslation();
  const copy = (zh: string, en: string) => (i18n.language?.startsWith("en") ? en : zh);
  const interaction = (((props.edge.data as Record<string, unknown>)?.interaction ?? defaultEdgeInteraction()) as Record<string, unknown>) || {};
  return (
    <div className="panel">
      <div className="panel-meta">
        <strong>EDGE</strong> / {props.edge.source} -&gt; {props.edge.target}
      </div>
      <h4>{copy("互动", "Interaction")}</h4>
      <label>
        {copy("方式", "Mode")}
        <select value={String(interaction.mode ?? "dialogue")} onChange={(e) => props.onChange("mode", e.target.value)}>
          {props.modes.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </label>
      <label>
        {copy("关系", "Relation")}
        <input value={String(interaction.relation ?? "peer")} onChange={(e) => props.onChange("relation", e.target.value)} />
      </label>
      <label>
        {copy("模板", "Template")}
        <textarea
          value={String(interaction.template ?? "")}
          onChange={(e) => props.onChange("template", e.target.value)}
          placeholder={copy("互动模板", "Interaction template")}
          rows={3}
        />
      </label>
      <label>
        {copy("强度", "Intensity")}
        <input
          type="number"
          value={String(interaction.intensity ?? 1)}
          onChange={(e) => props.onChange("intensity", Number(e.target.value || "1"))}
          min={0}
          max={10}
          step={0.1}
        />
      </label>
      <label className="advanced-toggle">
        <input
          type="checkbox"
          checked={Boolean(interaction.required ?? false)}
          onChange={(e) => props.onChange("required", e.target.checked)}
        />
        {copy("必须执行", "Required")}
      </label>
    </div>
  );
}

export function EnvironmentPanel(props: {
  environment: {
    profile: string;
    scenario: string;
    time_context?: string;
    spatial_context?: string;
    facts: string[];
    constraints: string[];
    glossary: Record<string, string>;
    context_book?: ContextBook;
  };
  nodes: Node[];
  onChange: (key: "profile" | "scenario" | "time_context" | "spatial_context" | "facts" | "constraints" | "glossary" | "context_book", value: unknown) => void;
}) {
  const { t, i18n } = useTranslation();
  const copy = (zh: string, en: string) => (i18n.language?.startsWith("en") ? en : zh);
  const book = props.environment.context_book ?? { entries: [], token_budget: 1600 };
  const updateEntries = (entries: BackgroundEntry[]) => props.onChange("context_book", { ...book, entries });
  const updateEntry = (id: string, patch: Partial<BackgroundEntry>) =>
    updateEntries(book.entries.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry)));
  const addEntry = () => {
    const id = `entry_${Date.now().toString(36)}`;
    updateEntries([
      ...book.entries,
      {
        id,
        title: "",
        content: "",
        kind: "background",
        visibility: { scope: "global", node_ids: [] },
        activation: { always: true, keywords: [], semantic_hint: "", related_entry_ids: [] },
        priority: 50,
        source: "user",
        enabled: true
      }
    ]);
  };
  return (
    <div className="panel">
      <div className="panel-meta">
        <strong>WORLD</strong> / {copy("环境引擎", "Environment")}
      </div>
      <label>
        {copy("世界概况", "Profile")}
        <textarea
          value={props.environment.profile}
          onChange={(e) => props.onChange("profile", e.target.value)}
          rows={2}
          placeholder={copy("背景概况", "World profile")}
        />
      </label>
      <div className="context-book">
        <div className="context-book-head">
          <strong>{t("backgroundBook")}</strong>
          <button className="btn compact" type="button" onClick={addEntry}>{t("addBackground")}</button>
        </div>
        <div className="context-book-list">
          {book.entries.map((entry, index) => (
            <div className="context-entry" key={entry.id}>
              <div className="context-entry-head">
                <span>{entry.title || entry.content.trim().slice(0, 24) || `${t("backgroundEntry")} ${index + 1}`}</span>
                <button className="icon-button" type="button" onClick={() => updateEntries(book.entries.filter((item) => item.id !== entry.id))} aria-label={String(t("delete"))}>×</button>
              </div>
              <textarea
                value={entry.content}
                onChange={(event) => updateEntry(entry.id, { content: event.target.value, title: event.target.value.trim().slice(0, 32) })}
                rows={3}
                placeholder={String(t("backgroundContent"))}
              />
              <div className="context-visibility">
                <button
                  className={`context-scope ${entry.visibility.scope === "global" ? "active" : ""}`}
                  type="button"
                  onClick={() => updateEntry(entry.id, { visibility: { scope: "global", node_ids: [] } })}
                >
                  {t("everyone")}
                </button>
                <button
                  className={`context-scope ${entry.visibility.scope === "private" ? "active" : ""}`}
                  type="button"
                  onClick={() => updateEntry(entry.id, { visibility: { scope: "private", node_ids: entry.visibility.node_ids } })}
                >
                  {t("selectedRoles")}
                </button>
              </div>
              {entry.visibility.scope === "private" ? (
                <div className="context-node-picks">
                  {props.nodes.filter((node) => !String(node.id).startsWith("summary_") && !String(node.id).startsWith("report_")).map((node) => {
                    const config = ((node.data as Record<string, unknown>)?.config ?? {}) as Record<string, unknown>;
                    const label = String(config.entity_name || config.profile || node.id);
                    const selected = entry.visibility.node_ids.includes(node.id);
                    return (
                      <button
                        className={`context-node-pick ${selected ? "active" : ""}`}
                        type="button"
                        key={node.id}
                        onClick={() => updateEntry(entry.id, {
                          visibility: {
                            scope: "private",
                            node_ids: selected
                              ? entry.visibility.node_ids.filter((id) => id !== node.id)
                              : [...entry.visibility.node_ids, node.id]
                          }
                        })}
                      >
                        {label}
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ))}
          {book.entries.length === 0 ? <div className="empty-hint">{t("noBackground")}</div> : null}
        </div>
      </div>
      <label>
        {copy("当前场景", "Scenario")}
        <textarea
          value={props.environment.scenario}
          onChange={(e) => props.onChange("scenario", e.target.value)}
          rows={3}
          placeholder={copy("当前场景", "Scenario")}
        />
      </label>
      <label>
        {copy("时间", "Time")}
        <textarea
          value={props.environment.time_context ?? ""}
          onChange={(e) => props.onChange("time_context", e.target.value)}
          rows={2}
          placeholder={copy("时间背景", "Time context")}
        />
      </label>
      <label>
        {copy("空间", "Space")}
        <textarea
          value={props.environment.spatial_context ?? ""}
          onChange={(e) => props.onChange("spatial_context", e.target.value)}
          rows={2}
          placeholder={copy("空间背景", "Spatial context")}
        />
      </label>
      <label>
        {copy("事实（每行一条）", "Facts (one per line)")}
        <textarea
          value={props.environment.facts.join("\n")}
          onChange={(e) => props.onChange("facts", e.target.value)}
          rows={4}
        />
      </label>
      <label>
        {copy("约束（每行一条）", "Constraints (one per line)")}
        <textarea
          value={props.environment.constraints.join("\n")}
          onChange={(e) => props.onChange("constraints", e.target.value)}
          rows={4}
        />
      </label>
      <label>
        {copy("术语表（JSON）", "Glossary (JSON)")}
        <textarea
          value={JSON.stringify(props.environment.glossary, null, 2)}
          onChange={(e) => props.onChange("glossary", e.target.value)}
          rows={5}
          placeholder="JSON"
        />
      </label>
    </div>
  );
}

export function NodeConfigPanel(props: {
  node: Node;
  spec?: NodeSpec;
  showAdvanced: boolean;
  onConfigChange: (key: string, value: string) => void;
  onInputChange: (key: string, value: string) => void;
}) {
  const { i18n } = useTranslation();
  const copy = (zh: string, en: string) => (i18n.language?.startsWith("en") ? en : zh);
  const nodeType = String((props.node.data as Record<string, unknown>)?.nodeType) as NodeType;
  const config = ((props.node.data as Record<string, unknown>)?.config ?? {}) as Record<string, unknown>;
  const inputs = ((props.node.data as Record<string, unknown>)?.inputs ?? {}) as Record<string, unknown>;
  const isEntityAgent = nodeType === "agent" && typeof config.entity_type === "string";
  const configFields = isEntityAgent ? fallbackConfigFields(nodeType) : props.spec?.config_fields ?? fallbackConfigFields(nodeType);
  const inputFields = props.spec?.input_fields ?? fallbackInputFields(nodeType);

  return (
    <div className="panel">
      <div className="panel-meta">
        <strong>{nodeType.toUpperCase()}</strong> / {props.node.id}
      </div>
      <h4>{copy("配置", "Config")}</h4>
      {configFields
        .filter((f) => props.showAdvanced || !f.advanced)
        .map((field) => renderField(localizeNodeField(field, i18n.language), config[field.key], props.onConfigChange))}
      <h4>{copy("输入", "Inputs")}</h4>
      {inputFields.length === 0 ? <div className="panel-meta">{copy("无", "none")}</div> : null}
      {inputFields
        .filter((f) => props.showAdvanced || !f.advanced)
        .map((field) => renderField(localizeNodeField(field, i18n.language), inputs[field.key], props.onInputChange))}
    </div>
  );
}

const nodeFieldLabelsZh: Record<string, string> = {
  "Integration Mode": "接入方式",
  "Agent ID": "Agent ID",
  "Endpoint URL": "接口地址",
  "Endpoint Path": "接口路径",
  "API Key": "API Key",
  "Timeout (ms)": "超时（毫秒）",
  "Max Tokens": "最大 Token",
  "Inject Background Context": "注入背景上下文",
  "Prompt Template": "提示词模板",
  Model: "模型",
  "Global Objective": "全局目标",
  "Style Guardrails": "风格约束",
  "GPRO Candidates": "GPRO 候选数",
  "Entity Type": "实体类型",
  "Entity Name": "实体名称",
  "Entity Profile": "实体设定",
  "Default Behavior Rule": "基础行为规则",
  "Execution Mode": "执行方式",
  "Runtime Role": "运行角色",
  "Model Connection": "模型连接",
  "Model Provider": "模型服务",
  "Compiled System Prompt": "系统提示词",
  "External Integration Mode": "外接方式",
  "External Agent URL": "外接 Agent 地址",
  "External Agent Path": "外接 Agent 路径",
  "External Agent API Key": "外接 Agent API Key",
  "External Timeout (ms)": "外接超时（毫秒）",
  Template: "模板",
  "Checkpoint Owner": "确认点负责人",
  "Question Template": "问题模板",
  "Response Required": "必须响应",
  "Tool Name": "工具名称",
  Expression: "表达式",
  "Task Prompt": "任务提示词",
  "Question Override": "问题覆盖",
  "Context Hint": "上下文提示",
  "Current Situation": "当前情境",
  Prompt: "提示词",
  Text: "文本"
};

function localizeNodeField(field: NodeFieldSpec, language: string): NodeFieldSpec {
  if (language.startsWith("en")) {
    return field;
  }
  return { ...field, label: nodeFieldLabelsZh[field.label] ?? field.label };
}

export function renderField(
  field: NodeFieldSpec,
  rawValue: unknown,
  onChange: (key: string, value: string) => void
): JSX.Element {
  const value =
    field.kind === "json" && typeof rawValue !== "string" ? JSON.stringify(rawValue ?? field.default ?? "", null, 2) : String(rawValue ?? "");
  if (field.kind === "textarea" || field.kind === "json") {
    return (
      <label key={field.key}>
        {field.label}
        <textarea
          value={value}
          placeholder={field.placeholder ?? ""}
          onChange={(e) => onChange(field.key, normalizeFieldValue(field.kind, e.target.value))}
          rows={field.kind === "json" ? 5 : 3}
        />
      </label>
    );
  }
  if (field.kind === "select") {
    return (
      <label key={field.key}>
        {field.label}
        <select value={value} onChange={(e) => onChange(field.key, normalizeFieldValue(field.kind, e.target.value))}>
          {field.options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <label key={field.key}>
      {field.label}
      <input
        type={field.kind === "password" ? "password" : field.kind === "number" ? "number" : "text"}
        value={value}
        placeholder={field.placeholder ?? ""}
        onChange={(e) => onChange(field.key, normalizeFieldValue(field.kind, e.target.value))}
      />
    </label>
  );
}

export function normalizeFieldValue(kind: NodeFieldSpec["kind"], value: string): string {
  if (kind === "json") {
    try {
      return JSON.stringify(JSON.parse(value));
    } catch {
      return value;
    }
  }
  return value;
}

export function fallbackConfigFields(nodeType: NodeType): NodeFieldSpec[] {
  if (nodeType === "external_agent") {
    return [
      {
        key: "integration_mode",
        label: "Integration Mode",
        kind: "select",
        required: false,
        default: "mock",
        options: ["mock", "http", "bridge_local"],
        advanced: false
      },
      { key: "agent_id", label: "Agent ID", kind: "text", required: false, default: "user_agent", options: [], advanced: false },
      { key: "endpoint_url", label: "Endpoint URL", kind: "text", required: false, default: "", options: [], advanced: false },
      { key: "endpoint_path", label: "Endpoint Path", kind: "text", required: false, default: "/agent/tasks", options: [], advanced: true },
      { key: "api_key", label: "API Key", kind: "password", required: false, default: "", options: [], advanced: true },
      { key: "timeout_ms", label: "Timeout (ms)", kind: "number", required: false, default: 60000, options: [], advanced: false },
      { key: "max_tokens", label: "Max Tokens", kind: "number", required: false, default: 1500, options: [], advanced: true },
      {
        key: "include_background_in_prompt",
        label: "Inject Background Context",
        kind: "select",
        required: false,
        default: "true",
        options: ["true", "false"],
        advanced: false
      },
      {
        key: "prompt_template",
        label: "Prompt Template",
        kind: "textarea",
        required: false,
        default: "{{prompt}}",
        options: [],
        advanced: true
      }
    ];
  }
  if (nodeType === "director") {
    return [
      { key: "model", label: "Model", kind: "text", required: false, default: "gpt-4o-mini", options: [], advanced: false },
      {
        key: "objective",
        label: "Global Objective",
        kind: "textarea",
        required: false,
        default: "Keep collaboration aligned with scenario goals while controlling cost.",
        options: [],
        advanced: false
      },
      {
        key: "style_guardrails",
        label: "Style Guardrails",
        kind: "textarea",
        required: false,
        default: "Be concise and role-consistent.",
        options: [],
        advanced: false
      },
      { key: "gpro_candidates", label: "GPRO Candidates", kind: "number", required: false, default: 3, options: [], advanced: false }
    ];
  }
  if (nodeType === "agent") {
    return [
      {
        key: "entity_type",
        label: "Entity Type",
        kind: "select",
        required: false,
        default: "individual",
        options: ["individual", "group", "organization", "environment", "event", "artifact"],
        advanced: false
      },
      { key: "entity_name", label: "Entity Name", kind: "text", required: false, default: "", options: [], advanced: false },
      {
        key: "entity_profile",
        label: "Entity Profile",
        kind: "textarea",
        required: false,
        default: "",
        options: [],
        advanced: false
      },
      {
        key: "behavior_prompt",
        label: "Default Behavior Rule",
        kind: "textarea",
        required: false,
        default: "You are simulating one concrete entity in the world.",
        options: [],
        advanced: false
      },
      {
        key: "execution_mode",
        label: "Execution Mode",
        kind: "select",
        required: false,
        default: "builtin_llm",
        options: ["builtin_llm", "external_agent", "manual"],
        advanced: false
      },
      { key: "role", label: "Runtime Role", kind: "text", required: false, default: "individual", options: [], advanced: true },
      { key: "model", label: "Model", kind: "text", required: false, default: "gpt-4o-mini", options: [], advanced: false },
      {
        key: "model_connection_mode",
        label: "Model Connection",
        kind: "select",
        required: false,
        default: "platform_default",
        options: ["platform_default", "node_override"],
        advanced: true
      },
      {
        key: "model_provider",
        label: "Model Provider",
        kind: "select",
        required: false,
        default: "openai_compatible",
        options: ["openai_compatible", "ollama", "llama_cpp", "lm_studio", "vllm", "localai", "tgi", "text_generation_webui"],
        advanced: true
      },
      {
        key: "system_prompt",
        label: "Compiled System Prompt",
        kind: "textarea",
        required: false,
        default: "You are simulating one concrete entity in the world.",
        options: [],
        advanced: true
      },
      {
        key: "external_integration_mode",
        label: "External Integration Mode",
        kind: "select",
        required: false,
        default: "mock",
        options: ["mock", "http", "bridge_local"],
        advanced: true
      },
      { key: "external_endpoint_url", label: "External Agent URL", kind: "text", required: false, default: "", options: [], advanced: true },
      {
        key: "external_endpoint_path",
        label: "External Agent Path",
        kind: "text",
        required: false,
        default: "/agent/tasks",
        options: [],
        advanced: true
      },
      { key: "external_api_key", label: "External Agent API Key", kind: "password", required: false, default: "", options: [], advanced: true },
      { key: "external_timeout_ms", label: "External Timeout (ms)", kind: "number", required: false, default: 60000, options: [], advanced: true }
    ];
  }
  if (nodeType === "prompt") {
    return [{ key: "template", label: "Template", kind: "textarea", required: false, default: "", options: [], advanced: false }];
  }
  if (nodeType === "human_checkpoint") {
    return [
      { key: "owner", label: "Checkpoint Owner", kind: "select", required: false, default: "director", options: ["director", "node"], advanced: false },
      {
        key: "question_template",
        label: "Question Template",
        kind: "textarea",
        required: false,
        default: "Please review current simulation state and provide intervention guidance.",
        options: [],
        advanced: false
      },
      { key: "required", label: "Response Required", kind: "select", required: false, default: "true", options: ["true", "false"], advanced: true }
    ];
  }
  if (nodeType === "tool") {
    return [{ key: "tool_name", label: "Tool Name", kind: "text", required: false, default: "echo", options: [], advanced: false }];
  }
  return [{ key: "expression", label: "Expression", kind: "text", required: false, default: "True", options: [], advanced: false }];
}

export function fallbackInputFields(nodeType: NodeType): NodeFieldSpec[] {
  if (nodeType === "external_agent") {
    return [{ key: "prompt", label: "Task Prompt", kind: "textarea", required: false, default: "{{input.task}}", options: [], advanced: false }];
  }
  if (nodeType === "human_checkpoint") {
    return [
      { key: "question", label: "Question Override", kind: "textarea", required: false, default: "", options: [], advanced: false },
      { key: "context_hint", label: "Context Hint", kind: "textarea", required: false, default: "{{input.task}}", options: [], advanced: false }
    ];
  }
  if (nodeType === "director") {
    return [{ key: "situation", label: "Current Situation", kind: "textarea", required: false, default: "{{input.task}}", options: [], advanced: false }];
  }
  if (nodeType === "agent") {
    return [{ key: "prompt", label: "Prompt", kind: "textarea", required: false, default: "{{input.task}}", options: [], advanced: false }];
  }
  if (nodeType === "tool") {
    return [{ key: "text", label: "Text", kind: "text", required: false, default: "{{input.task}}", options: [], advanced: false }];
  }
  return [];
}

export function parseRoleCards(raw: unknown): RoleCard[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw
    .map((item) => item as Record<string, unknown>)
    .map((item, idx) => ({
      id: String(item.id ?? `role_${idx}`),
      name: String(item.name ?? ""),
      archetype: String(item.archetype ?? ""),
      goal: String(item.goal ?? ""),
      style: String(item.style ?? ""),
      boundaries: String(item.boundaries ?? "")
    }))
    .filter((row) => row.name.trim().length > 0);
}

export function buildRelationshipRows(edges: Edge[]): Array<{ id: string; source: string; target: string; mode: string; relation: string; intensity: string }> {
  return edges.map((edge) => {
    const interaction = (((edge.data as Record<string, unknown> | undefined)?.interaction ?? defaultEdgeInteraction()) as Record<string, unknown>) || {};
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      mode: String(interaction.mode ?? "dialogue"),
      relation: String(interaction.relation ?? "peer"),
      intensity: String(interaction.intensity ?? 1)
    };
  });
}

export function parseSimulationSemantics(raw: unknown): { mode: string; confidence: number; reason: string } {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { mode: "unknown", confidence: 0, reason: "No director semantic inference yet." };
  }
  const obj = raw as Record<string, unknown>;
  return {
    mode: String(obj.mode ?? "unknown"),
    confidence: Math.max(0, Math.min(1, Number(obj.confidence ?? 0) || 0)),
    reason: String(obj.reason ?? "")
  };
}

export function findRoleCardForNode(node: Node, roleCards: RoleCard[]): RoleCard | null {
  const data = (node.data as Record<string, unknown> | undefined) ?? {};
  const config = (data.config as Record<string, unknown> | undefined) ?? {};
  const role = String(config.role ?? "").trim().toLowerCase();
  const label = String(data.label ?? "").trim().toLowerCase();
  if (!role && !label) {
    return null;
  }
  const byRole = roleCards.find((c) => c.name.trim().toLowerCase() === role);
  if (byRole) {
    return byRole;
  }
  const byLabel = roleCards.find((c) => label.includes(c.name.trim().toLowerCase()));
  return byLabel ?? null;
}

export function decisionActionToText(action: "continue" | "adjust_direction" | "reduce_cost" | "inject_event"): string {
  if (action === "adjust_direction") {
    return "Director decision: adjust direction and tighten scope.";
  }
  if (action === "reduce_cost") {
    return "Director decision: reduce cost mode, preserve core signal only.";
  }
  if (action === "inject_event") {
    return "Director decision: inject a new event and re-evaluate downstream.";
  }
  return "Director decision: continue with current direction.";
}

export function toBroadcastLine(
  row: { event: string; speaker: string; text: string },
  mode: UiMode,
  tone: "news" | "warroom" | "calm"
): string {
  const fullText = row.text.replace(/\s+/g, " ").trim();
  const txt = fullText.length > 180 ? `${fullText.slice(0, 177).trimEnd()}...` : fullText;
  const isCritical = row.event === "failed" || row.event === "waiting_human" || row.event === "director_overridden";
  if (tone === "warroom") {
    if (isCritical) {
      return `[ALERT] ${row.speaker}: ${txt}`;
    }
    return `[OPS] ${row.speaker}: ${txt}`;
  }
  if (tone === "calm") {
    return `${row.speaker}: ${txt}`;
  }
  void mode;
  return `${row.speaker}: ${txt}`;
}

export function getModeVisibility(mode: UiMode) {
  if (mode === "roleplay") {
    return {
      director: true,
      glimpse: true,
      human: true,
      monitor: true,
      simulation: true,
      trace: true,
      timeline: true,
      metrics: true,
      compare: false,
      report: true,
      ops: false
    };
  }
  if (mode === "custom") {
    return {
      director: true,
      glimpse: true,
      human: true,
      monitor: true,
      simulation: true,
      trace: true,
      timeline: true,
      metrics: true,
      compare: true,
      report: true,
      ops: true
    };
  }
  return {
    director: true,
    glimpse: true,
    human: true,
    monitor: true,
    simulation: true,
    trace: true,
    timeline: true,
    metrics: true,
    compare: true,
    report: true,
    ops: true
  };
}


