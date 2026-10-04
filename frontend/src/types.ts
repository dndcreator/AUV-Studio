export type NodeType = "agent" | "prompt" | "tool" | "condition" | "director" | "external_agent" | "human_checkpoint";

export type WorkflowNode = {
  id: string;
  type: NodeType;
  position: { x: number; y: number };
  config: Record<string, unknown>;
  inputs: Record<string, unknown>;
};

export type WorkflowEdge = {
  id: string;
  source: string;
  target: string;
  condition?: string | null;
  interaction?: {
    mode: "report" | "instruction" | "feedback" | "dialogue" | "handoff";
    relation: string;
    template?: string | null;
    required: boolean;
    intensity: number;
  } | null;
};

export type WorkflowDefinition = {
  id: string;
  name: string;
  version: number;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  entry_nodes: string[];
  environment: {
    profile: string;
    scenario: string;
    time_context?: string;
    spatial_context?: string;
    facts: string[];
    constraints: string[];
    glossary: Record<string, string>;
    context_book?: ContextBook;
    simulation?: Record<string, unknown>;
  };
};

export type BackgroundEntry = {
  id: string;
  title: string;
  content: string;
  kind: "background" | "entity" | "rule" | "fact" | "knowledge" | "method" | "belief" | "rumor" | "assumption" | "instruction";
  visibility: {
    scope: "global" | "private";
    node_ids: string[];
  };
  activation: {
    always: boolean;
    keywords: string[];
    semantic_hint: string;
    related_entry_ids: string[];
  };
  priority: number;
  source: string;
  enabled: boolean;
};

export type ContextBook = {
  entries: BackgroundEntry[];
  token_budget: number;
};

export type WorkflowSummary = {
  id: string;
  name: string;
  version: number;
  updated_at?: string | null;
};

export type RunEvent = {
  seq?: number;
  run_id: string;
  node_id: string;
  event:
    | "queued"
    | "running"
    | "succeeded"
    | "failed"
    | "skipped"
    | "waiting_human"
    | "human_resumed"
    | "director_overridden"
    | "director_corrected"
    | "director_vote_started"
    | "director_vote_cast"
    | "director_vote_finished"
    | "stop_requested"
    | "stopped"
    | "nodes_activated"
    | "episode_audited"
    | "dynamic_state_updated"
    | "dynamic_state_failed";
  timestamp: string;
  duration_ms?: number | null;
  payload: Record<string, unknown>;
};

export type TraceEventContext = {
  run_id: string;
  seq: number;
  event: RunEvent;
  parent_events: RunEvent[];
  caused_by: string;
  context_snapshot: Record<string, unknown>;
  chain_ok: boolean;
};

export type RunCheckpoint = {
  seq: number;
  node_id: string;
  event: RunEvent["event"];
  timestamp: string;
  label: string;
  summary: string;
  can_rollback: boolean;
};

export type RunCheckpoints = {
  run_id: string;
  checkpoints: RunCheckpoint[];
};

export type RollbackResponse = {
  run_id: string;
  status: string;
  source_run_id: string;
  event_seq: number;
  retry_from_node: string;
};

export type AuditVerify = {
  run_id: string;
  verified: boolean;
  total_events: number;
  checked_events: number;
  broken_at_seq: number | null;
  message: string;
};

export type RunDetail = {
  id: string;
  workflow_id: string;
  status: "pending" | "running" | "waiting_human" | "stopping" | "stopped" | "succeeded" | "failed";
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  error: Record<string, unknown> | null;
  retry_from_run_id?: string | null;
  retry_from_node?: string | null;
  started_at: string;
  ended_at: string | null;
  node_runs: Array<{
    node_id: string;
    status: string;
    started_at: string | null;
    ended_at: string | null;
    duration_ms: number | null;
    input: Record<string, unknown>;
    output: Record<string, unknown>;
    error: Record<string, unknown> | null;
  }>;
};

export type FullLog = {
  run_id: string;
  format: "json" | "markdown";
  content: string;
  event_count: number;
  node_count: number;
};

export type RunMetrics = {
  run_id: string;
  status: "pending" | "running" | "waiting_human" | "stopping" | "stopped" | "succeeded" | "failed";
  duration_ms: number | null;
  total_events: number;
  total_nodes: number;
  succeeded_nodes: number;
  failed_nodes: number;
  skipped_nodes: number;
  total_interactions: number;
  director_guidance_count: number;
  director_avg_score: number | null;
  director_effect: {
    first_score: number | null;
    last_score: number | null;
    delta_score: number | null;
    improved: boolean;
  };
  estimated_prompt_chars: number;
  estimated_completion_chars: number;
  estimated_token_usage: number;
};

export type RunCompare = {
  run_a: string;
  run_b: string;
  duration_ms_a: number | null;
  duration_ms_b: number | null;
  estimated_token_usage_a: number;
  estimated_token_usage_b: number;
  succeeded_nodes_a: number;
  succeeded_nodes_b: number;
  failed_nodes_a: number;
  failed_nodes_b: number;
  token_delta: number;
  duration_delta_ms: number | null;
  winner: "a" | "b" | "tie";
};

export type RunReportRequest = {
  mode: "briefing" | "narrative" | "story";
  title?: string;
  style_prompt?: string;
  length: "short" | "medium" | "long";
  audience?: string;
};

export type RunReport = {
  run_id: string;
  mode: "briefing" | "narrative" | "story";
  title: string;
  report_markdown: string;
  sections: Array<{
    heading: string;
    summary: string;
    evidence_refs: string[];
  }>;
  evidence_index: string[];
};

export type TemplateSummary = {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  updated_at: string | null;
};

export type TemplateDetail = {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  workflow: WorkflowDefinition;
};

export type TemplateCreateRequest = {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  workflow: WorkflowDefinition;
};

export type ObservabilitySnapshot = {
  timestamp: string;
  http: {
    requests_1m: number;
    requests_5m: number;
    error_count_5m: number;
    error_rate_5m: number;
    latency_p50_ms_5m: number | null;
    latency_p95_ms_5m: number | null;
    slow_requests_5m: number;
  };
  runtime: {
    runs_total: number;
    runs_failed_24h: number;
    runs_succeeded_24h: number;
    avg_run_duration_ms_24h: number | null;
    slow_nodes_24h: number;
    node_failures_24h: number;
  };
  alerts: Array<{
    code: string;
    severity: "info" | "warn" | "critical";
    message: string;
  }>;
};

export type QueueStatus = {
  queued: number;
  active: number;
  completed: number;
  failed: number;
  avg_wait_ms: number;
  worker_count: number;
  active_run_ids: string[];
};

export type MemoryItem = {
  id: number;
  workflow_id: string;
  run_id: string;
  node_id: string;
  role: string;
  content: string;
  tags: string[];
  kind: "fact" | "state" | "character" | "other";
  scope: string;
  subject: string;
  importance: number;
  confidence: number;
  source_event_seq: number | null;
  created_at: string;
};

export type ModelProviderKind =
  | "openai_compatible"
  | "ollama"
  | "llama_cpp"
  | "lm_studio"
  | "vllm"
  | "localai"
  | "tgi"
  | "text_generation_webui";

export type ModelConfig = {
  provider: ModelProviderKind | string;
  base_url: string;
  default_model: string;
  has_api_key: boolean;
  masked_api_key: string;
  requires_api_key: boolean;
  source: "database" | "environment" | "default";
};

export type ModelConfigRequest = {
  provider?: ModelProviderKind;
  base_url: string;
  default_model: string;
  api_key?: string | null;
};

export type ModelConfigTestResponse = {
  ok: boolean;
  message: string;
  latency_ms: number | null;
};

export type MediaType = "image" | "video";
export type MediaProvider = "openai_compatible" | "openai_video" | "generic_http";

export type MediaConfig = {
  media_type: MediaType;
  provider: MediaProvider;
  base_url: string;
  model: string;
  endpoint_path: string;
  status_path: string;
  has_api_key: boolean;
  masked_api_key: string;
  requires_api_key: boolean;
  source: "database" | "environment" | "default";
};

export type MediaConfigRequest = {
  media_type: MediaType;
  provider: MediaProvider;
  base_url: string;
  model: string;
  api_key?: string | null;
  endpoint_path?: string;
  status_path?: string;
};

export type MediaAsset = {
  asset_id: string;
  media_type: MediaType;
  status: "queued" | "in_progress" | "completed" | "failed";
  prompt: string;
  url: string;
  data_url: string;
  job_id: string;
  mime_type: string;
  error: string;
};

export type MediaGeneration = {
  generation_id: string;
  run_id: string;
  media_type: MediaType;
  provider: MediaProvider;
  model: string;
  status: "queued" | "in_progress" | "completed" | "failed" | "partial";
  assets: MediaAsset[];
};

export type PlanCompileRequest = {
  plan_text: string;
  mode: "auto" | "research" | "roleplay" | "custom" | "collab";
  submode?: string;
  max_agents: number;
  language: "zh" | "en";
  evidence_pack?: EvidencePack;
};

export type EvidenceItem = {
  id: string;
  name: string;
  kind: "document" | "table" | "structured";
  content: string;
};

export type EvidencePack = {
  version: number;
  package_name: string;
  items: EvidenceItem[];
  skipped: Array<{ name: string; reason: string }>;
  errors: Array<{ name: string; reason: string }>;
  summary: { parsed_files: number; skipped_files: number; failed_files: number; characters: number };
};

export type PlanCompileResponse = {
  workflow: WorkflowDefinition;
  extracted: Record<string, unknown>;
  rationale: string;
  simulation_blueprint: Record<string, unknown>;
};

export type HumanResponseRequest = {
  node_id?: string;
  response: string;
  responder?: string;
  metadata?: Record<string, unknown>;
};

export type DirectorCapability = {
  capability_id: string;
  title: string;
  description: string;
  args_schema: Record<string, unknown>;
};

export type DirectorCommandRequest = {
  text: string;
  scope?: "global" | "phase" | "node";
  target_node_id?: string | null;
  strict?: boolean;
};

export type DirectorCommandResponse = {
  run_id: string;
  accepted: boolean;
  applied_capability: string;
  normalized_args: Record<string, unknown>;
  guidance: string;
  event_seq?: number | null;
};

export type ModeContract = {
  mode: string;
  description: string;
  objectives: string[];
  required_outputs: string[];
  submodes: Record<string, Record<string, unknown>>;
};

export type NodeFieldSpec = {
  key: string;
  label: string;
  kind: "text" | "textarea" | "select" | "number" | "json" | "password";
  required: boolean;
  default: unknown;
  options: string[];
  placeholder?: string | null;
  advanced: boolean;
};

export type NodeSpec = {
  type: NodeType;
  title: string;
  description: string;
  config_fields: NodeFieldSpec[];
  input_fields: NodeFieldSpec[];
};
