import type {
  AuditVerify,
  DirectorCapability,
  EvidencePack,
  DirectorCommandRequest,
  DirectorCommandResponse,
  FullLog,
  NodeSpec,
  MemoryItem,
  MediaConfig,
  MediaConfigRequest,
  MediaGeneration,
  MediaType,
  ModelConfig,
  ModelConfigRequest,
  ModelConfigTestResponse,
  ModeContract,
  PlanCompileRequest,
  PlanCompileResponse,
  HumanResponseRequest,
  RunCompare,
  RunCheckpoints,
  RunDetail,
  RunEvent,
  RunMetrics,
  RunReport,
  RunReportRequest,
  RollbackResponse,
  TraceEventContext,
  TemplateCreateRequest,
  TemplateDetail,
  TemplateSummary,
  ObservabilitySnapshot,
  QueueStatus,
  WorkflowDefinition,
  WorkflowSummary
} from "./types";

const API_BASE = "http://localhost:8000/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const api = {
  parseEvidence: async (file: File): Promise<EvidencePack> => {
    const res = await fetch(`${API_BASE}/evidence/parse`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
      body: file
    });
    if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);
    return (await res.json()) as EvidencePack;
  },
  createWorkflow: (data: WorkflowDefinition) =>
    request<WorkflowDefinition>("/workflows", {
      method: "POST",
      body: JSON.stringify(data)
    }),
  updateWorkflow: (data: WorkflowDefinition) =>
    request<WorkflowDefinition>(`/workflows/${data.id}`, {
      method: "PUT",
      body: JSON.stringify(data)
    }),
  getWorkflow: (id: string) => request<WorkflowDefinition>(`/workflows/${id}`),
  listWorkflows: () => request<WorkflowSummary[]>("/workflows"),
  getNodeSpecs: () => request<NodeSpec[]>("/node-specs"),
  getInteractionModes: () => request<string[]>("/interaction-modes"),
  getModelConfig: () => request<ModelConfig>("/model-config"),
  saveModelConfig: (payload: ModelConfigRequest) =>
    request<ModelConfig>("/model-config", {
      method: "PUT",
      body: JSON.stringify({ provider: "openai_compatible", ...payload })
    }),
  testModelConfig: (payload: ModelConfigRequest) =>
    request<ModelConfigTestResponse>("/model-config/test", {
      method: "POST",
      body: JSON.stringify({ provider: "openai_compatible", ...payload })
    }),
  getMediaConfig: (mediaType: MediaType) => request<MediaConfig>(`/media-config/${mediaType}`),
  saveMediaConfig: (mediaType: MediaType, payload: MediaConfigRequest) =>
    request<MediaConfig>(`/media-config/${mediaType}`, {
      method: "PUT",
      body: JSON.stringify({ ...payload, media_type: mediaType })
    }),
  testMediaConfig: (mediaType: MediaType, payload: MediaConfigRequest) =>
    request<ModelConfigTestResponse>(`/media-config/${mediaType}/test`, {
      method: "POST",
      body: JSON.stringify({ ...payload, media_type: mediaType })
    }),
  generateRunMedia: (
    runId: string,
    payload: {
      media_type: MediaType;
      source_text?: string;
      style_prompt?: string;
      shot_count?: number;
      size?: string;
      quality?: string;
      seconds?: number;
    }
  ) =>
    request<MediaGeneration>(`/runs/${runId}/media`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getMediaGeneration: (generationId: string) => request<MediaGeneration>(`/media/generations/${generationId}`),
  getModeContracts: () => request<ModeContract[]>("/mode-contracts"),
  getDirectorCapabilities: () => request<DirectorCapability[]>("/director/capabilities"),
  listTemplates: () => request<TemplateSummary[]>("/templates"),
  getTemplate: (id: string) => request<TemplateDetail>(`/templates/${id}`),
  createTemplate: (payload: TemplateCreateRequest) =>
    request<TemplateDetail>("/templates", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  compileTemplate: (payload: PlanCompileRequest) =>
    request<PlanCompileResponse>("/templates/compile", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  runWorkflow: (workflowId: string, input: Record<string, unknown> = {}) =>
    request<{ run_id: string; status: string }>(`/workflows/${workflowId}/run`, {
      method: "POST",
      body: JSON.stringify({ input })
    }),
  retryRun: (runId: string, nodeId?: string) =>
    request<{ run_id: string; status: string }>(`/runs/${runId}/retry`, {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId ?? null })
    }),
  getRunCheckpoints: (runId: string, limit = 80) => request<RunCheckpoints>(`/runs/${runId}/checkpoints?limit=${limit}`),
  rollbackRun: (runId: string, eventSeq: number, reason?: string) =>
    request<RollbackResponse>(`/runs/${runId}/rollback`, {
      method: "POST",
      body: JSON.stringify({ event_seq: eventSeq, reason: reason ?? "" })
    }),
  respondHumanCheckpoint: (runId: string, payload: HumanResponseRequest) =>
    request<{ run_id: string; status: string }>(`/runs/${runId}/human-response`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  sendDirectorCommand: (runId: string, payload: DirectorCommandRequest) =>
    request<DirectorCommandResponse>(`/runs/${runId}/director-command`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  stopRun: (runId: string) =>
    request<{ run_id: string; status: string }>(`/runs/${runId}/stop`, {
      method: "POST"
    }),
  getRun: (runId: string) => request<RunDetail>(`/runs/${runId}`),
  getRunEvents: (runId: string, afterSeq: number) =>
    request<{ run_id: string; events: RunEvent[] }>(`/runs/${runId}/events?after_seq=${afterSeq}&limit=500`),
  getFullLog: (runId: string, format: "markdown" | "json" = "markdown") =>
    request<FullLog>(`/runs/${runId}/full-log?format=${format}`),
  getRunTraceEvent: (runId: string, seq: number) => request<TraceEventContext>(`/runs/${runId}/trace/${seq}`),
  verifyRunAudit: (runId: string) => request<AuditVerify>(`/runs/${runId}/audit/verify`),
  getRunMetrics: (runId: string) => request<RunMetrics>(`/runs/${runId}/metrics`),
  compareRuns: (runA: string, runB: string) =>
    request<RunCompare>(`/compare-runs?run_a=${encodeURIComponent(runA)}&run_b=${encodeURIComponent(runB)}`),
  generateReport: (runId: string, payload: RunReportRequest) =>
    request<RunReport>(`/runs/${runId}/report`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getObservability: () => request<ObservabilitySnapshot>("/observability"),
  getQueueStatus: () => request<QueueStatus>("/queue/status"),
  getMemories: (workflowId: string, limit = 100) =>
    request<MemoryItem[]>(`/workflows/${workflowId}/memories?limit=${limit}`),
  clearMemories: (workflowId: string) =>
    request<{ workflow_id: string; deleted: number }>(`/workflows/${workflowId}/memories`, {
      method: "DELETE"
    })
};
