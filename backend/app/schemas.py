from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


NodeType = Literal["agent", "prompt", "tool", "condition", "director", "external_agent", "human_checkpoint"]
RunEventType = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "waiting_human",
    "human_resumed",
    "director_overridden",
    "director_corrected",
    "director_vote_started",
    "director_vote_cast",
    "director_vote_finished",
    "stop_requested",
    "stopped",
    "nodes_activated",
    "episode_audited",
    "context_pack_activated",
    "dynamic_state_updated",
    "dynamic_state_failed",
]
RunStatus = Literal["pending", "running", "waiting_human", "stopping", "stopped", "succeeded", "failed"]


class NodePosition(BaseModel):
    x: float
    y: float


class WorkflowNode(BaseModel):
    id: str
    type: NodeType
    position: NodePosition
    config: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)


class InteractionSpec(BaseModel):
    mode: Literal["report", "instruction", "feedback", "dialogue", "handoff"] = "dialogue"
    relation: str = "peer"
    template: str | None = None
    required: bool = False
    intensity: float = 1.0


class WorkflowEdge(BaseModel):
    id: str
    source: str
    target: str
    condition: str | None = None
    interaction: InteractionSpec | None = None


BackgroundKind = Literal["background", "entity", "rule", "fact", "knowledge", "method", "belief", "rumor", "assumption", "instruction"]


class BackgroundVisibility(BaseModel):
    scope: Literal["global", "private"] = "global"
    node_ids: list[str] = Field(default_factory=list)


class BackgroundActivation(BaseModel):
    always: bool = True
    keywords: list[str] = Field(default_factory=list)
    semantic_hint: str = ""
    related_entry_ids: list[str] = Field(default_factory=list)


class BackgroundEntry(BaseModel):
    id: str
    title: str = ""
    content: str
    kind: BackgroundKind = "background"
    visibility: BackgroundVisibility = Field(default_factory=BackgroundVisibility)
    activation: BackgroundActivation = Field(default_factory=BackgroundActivation)
    priority: int = Field(default=50, ge=0, le=100)
    source: str = "user"
    enabled: bool = True


class ContextBookSpec(BaseModel):
    entries: list[BackgroundEntry] = Field(default_factory=list)
    token_budget: int = Field(default=1600, ge=128, le=32000)


class EnvironmentSpec(BaseModel):
    profile: str = ""
    scenario: str = ""
    time_context: str = ""
    spatial_context: str = ""
    facts: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    context_book: ContextBookSpec = Field(default_factory=ContextBookSpec)
    simulation: dict[str, Any] = Field(default_factory=dict)


class WorkflowDefinition(BaseModel):
    id: str
    name: str
    version: int = 1
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    entry_nodes: list[str] = Field(default_factory=list)
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)


class WorkflowSummary(BaseModel):
    id: str
    name: str
    version: int
    updated_at: datetime | None = None


class RunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    retry_from_run_id: str | None = None
    retry_from_node: str | None = None


class RetryRequest(BaseModel):
    node_id: str | None = None


class RollbackRequest(BaseModel):
    event_seq: int
    reason: str | None = None


class RollbackResponse(BaseModel):
    run_id: str
    status: str
    source_run_id: str
    event_seq: int
    retry_from_node: str


class HumanResponseRequest(BaseModel):
    node_id: str | None = None
    response: str
    responder: str = "user"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DirectorCommandRequest(BaseModel):
    text: str
    scope: Literal["global", "phase", "node"] = "global"
    target_node_id: str | None = None
    strict: bool = False


class DirectorCapability(BaseModel):
    capability_id: str
    title: str
    description: str
    args_schema: dict[str, Any] = Field(default_factory=dict)


class DirectorCommandResponse(BaseModel):
    run_id: str
    accepted: bool
    applied_capability: str
    normalized_args: dict[str, Any] = Field(default_factory=dict)
    guidance: str = ""
    event_seq: int | None = None


class RunEvent(BaseModel):
    seq: int | None = None
    run_id: str
    node_id: str
    event: RunEventType
    timestamp: datetime
    duration_ms: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class NodeRun(BaseModel):
    node_id: str
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


class RunDetail(BaseModel):
    id: str
    workflow_id: str
    status: RunStatus
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    retry_from_run_id: str | None = None
    retry_from_node: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    node_runs: list[NodeRun] = Field(default_factory=list)


class EventsResponse(BaseModel):
    run_id: str
    events: list[RunEvent]


class FullLogResponse(BaseModel):
    run_id: str
    format: Literal["json", "markdown"]
    content: str
    event_count: int = 0
    node_count: int = 0


class TraceEventContextResponse(BaseModel):
    run_id: str
    seq: int
    event: RunEvent
    parent_events: list[RunEvent] = Field(default_factory=list)
    caused_by: str = ""
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    chain_ok: bool = True


class RunCheckpoint(BaseModel):
    seq: int
    node_id: str
    event: RunEventType
    timestamp: datetime
    label: str
    summary: str = ""
    can_rollback: bool = True


class RunCheckpointsResponse(BaseModel):
    run_id: str
    checkpoints: list[RunCheckpoint] = Field(default_factory=list)


class AuditVerifyResponse(BaseModel):
    run_id: str
    verified: bool
    total_events: int = 0
    checked_events: int = 0
    broken_at_seq: int | None = None
    message: str = ""


class DirectorEffect(BaseModel):
    first_score: float | None = None
    last_score: float | None = None
    delta_score: float | None = None
    improved: bool = False


class RunMetricsResponse(BaseModel):
    run_id: str
    status: RunStatus
    duration_ms: int | None = None
    total_events: int = 0
    total_nodes: int = 0
    succeeded_nodes: int = 0
    failed_nodes: int = 0
    skipped_nodes: int = 0
    total_interactions: int = 0
    director_guidance_count: int = 0
    director_avg_score: float | None = None
    director_effect: DirectorEffect = Field(default_factory=DirectorEffect)
    estimated_prompt_chars: int = 0
    estimated_completion_chars: int = 0
    estimated_token_usage: int = 0


class RunCompareResponse(BaseModel):
    run_a: str
    run_b: str
    duration_ms_a: int | None = None
    duration_ms_b: int | None = None
    estimated_token_usage_a: int = 0
    estimated_token_usage_b: int = 0
    succeeded_nodes_a: int = 0
    succeeded_nodes_b: int = 0
    failed_nodes_a: int = 0
    failed_nodes_b: int = 0
    token_delta: int = 0
    duration_delta_ms: int | None = None
    winner: Literal["a", "b", "tie"] = "tie"


ReportMode = Literal["briefing", "narrative", "story"]
ReportLength = Literal["short", "medium", "long"]


class RunReportRequest(BaseModel):
    mode: ReportMode = "briefing"
    title: str | None = None
    style_prompt: str | None = None
    length: ReportLength = "medium"
    audience: str | None = None


class ReportSection(BaseModel):
    heading: str
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)


class RunReportResponse(BaseModel):
    run_id: str
    mode: ReportMode
    title: str
    report_markdown: str
    sections: list[ReportSection] = Field(default_factory=list)
    evidence_index: list[str] = Field(default_factory=list)


class PerformanceAlert(BaseModel):
    code: str
    severity: Literal["info", "warn", "critical"]
    message: str


class ObservabilityHttpMetrics(BaseModel):
    requests_1m: int = 0
    requests_5m: int = 0
    error_count_5m: int = 0
    error_rate_5m: float = 0.0
    latency_p50_ms_5m: int | None = None
    latency_p95_ms_5m: int | None = None
    slow_requests_5m: int = 0


class ObservabilityRunMetrics(BaseModel):
    runs_total: int = 0
    runs_failed_24h: int = 0
    runs_succeeded_24h: int = 0
    avg_run_duration_ms_24h: int | None = None
    slow_nodes_24h: int = 0
    node_failures_24h: int = 0


class ObservabilitySnapshot(BaseModel):
    timestamp: datetime
    http: ObservabilityHttpMetrics
    runtime: ObservabilityRunMetrics
    alerts: list[PerformanceAlert] = Field(default_factory=list)


class QueueStatusResponse(BaseModel):
    queued: int = 0
    active: int = 0
    completed: int = 0
    failed: int = 0
    avg_wait_ms: int = 0
    worker_count: int = 0
    active_run_ids: list[str] = Field(default_factory=list)


class TemplateSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class TemplateCreateRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    workflow: WorkflowDefinition


class TemplateDetail(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    workflow: WorkflowDefinition


PlanMode = Literal["auto", "research", "roleplay", "custom", "collab"]
ResearchSubmode = Literal["auto", "simulation", "research", "consulting"]


class PlanCompileRequest(BaseModel):
    plan_text: str
    mode: PlanMode = "auto"
    submode: str | None = None
    max_agents: int = 6
    language: Literal["zh", "en"] = "zh"
    evidence_pack: dict[str, Any] = Field(default_factory=dict)


class PlanCompileResponse(BaseModel):
    workflow: WorkflowDefinition
    extracted: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    simulation_blueprint: dict[str, Any] = Field(default_factory=dict)


class ModeContract(BaseModel):
    mode: str
    description: str
    objectives: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
    submodes: dict[str, dict[str, Any]] = Field(default_factory=dict)


class MemoryItem(BaseModel):
    id: int
    workflow_id: str
    run_id: str
    node_id: str
    role: str = ""
    content: str
    tags: list[str] = Field(default_factory=list)
    kind: Literal["fact", "state", "character", "other"] = "fact"
    scope: str = "node"
    subject: str = ""
    importance: float = 0.5
    confidence: float = 0.7
    source_event_seq: int | None = None
    created_at: datetime


class MemoryClearResponse(BaseModel):
    workflow_id: str
    deleted: int


class ModelConfigRequest(BaseModel):
    provider: Literal[
        "openai_compatible", "ollama", "llama_cpp", "lm_studio", "vllm", "localai", "tgi", "text_generation_webui"
    ] = "openai_compatible"
    base_url: str = ""
    default_model: str = ""
    api_key: str | None = None


class ModelConfigResponse(BaseModel):
    provider: str = "openai_compatible"
    base_url: str
    default_model: str
    has_api_key: bool = False
    masked_api_key: str = ""
    requires_api_key: bool = True
    source: Literal["database", "environment", "default"] = "default"


class ModelConfigTestRequest(BaseModel):
    provider: Literal[
        "openai_compatible", "ollama", "llama_cpp", "lm_studio", "vllm", "localai", "tgi", "text_generation_webui"
    ] = "openai_compatible"
    base_url: str
    default_model: str
    api_key: str | None = None


class ModelConfigTestResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: int | None = None


MediaType = Literal["image", "video"]
MediaProvider = Literal["openai_compatible", "openai_video", "generic_http"]


class MediaConfigRequest(BaseModel):
    media_type: MediaType
    provider: MediaProvider
    base_url: str
    model: str
    api_key: str | None = None
    endpoint_path: str = ""
    status_path: str = ""


class MediaConfigResponse(BaseModel):
    media_type: MediaType
    provider: MediaProvider
    base_url: str
    model: str
    endpoint_path: str = ""
    status_path: str = ""
    has_api_key: bool = False
    masked_api_key: str = ""
    requires_api_key: bool = True
    source: Literal["database", "environment", "default"] = "default"


class MediaConfigTestResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: int | None = None


class MediaGenerateRequest(BaseModel):
    media_type: MediaType
    source_text: str = Field(default="", max_length=50000)
    style_prompt: str = Field(default="", max_length=4000)
    shot_count: int = Field(default=1, ge=1, le=4)
    size: str = "1024x1024"
    quality: str = "standard"
    seconds: int = Field(default=4, ge=1, le=30)


class MediaAsset(BaseModel):
    asset_id: str
    media_type: MediaType
    status: Literal["queued", "in_progress", "completed", "failed"]
    prompt: str
    url: str = ""
    data_url: str = ""
    job_id: str = ""
    mime_type: str = ""
    error: str = ""


class MediaGenerationResponse(BaseModel):
    generation_id: str
    run_id: str
    media_type: MediaType
    provider: MediaProvider
    model: str
    status: Literal["queued", "in_progress", "completed", "failed", "partial"]
    assets: list[MediaAsset] = Field(default_factory=list)


class ExternalAgentContext(BaseModel):
    environment: dict[str, Any] = Field(default_factory=dict)
    global_guidance: str = ""
    interactions: list[dict[str, Any]] = Field(default_factory=list)
    task_goal: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)


class ExternalAgentTaskRequest(BaseModel):
    protocol_version: Literal["1.0"] = "1.0"
    task_id: str
    run_id: str
    node_id: str
    context: ExternalAgentContext
    input: dict[str, Any]
    constraints: dict[str, Any] = Field(default_factory=dict)
    reply_mode: Literal["sync"] = "sync"


class ExternalAgentTaskResponse(BaseModel):
    task_id: str
    status: Literal["succeeded", "failed"]
    output: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class NodeFieldSpec(BaseModel):
    key: str
    label: str
    kind: Literal["text", "textarea", "select", "number", "json", "password"]
    required: bool = False
    default: Any = None
    options: list[str] = Field(default_factory=list)
    placeholder: str | None = None
    advanced: bool = False


class NodeSpec(BaseModel):
    type: NodeType
    title: str
    description: str
    config_fields: list[NodeFieldSpec] = Field(default_factory=list)
    input_fields: list[NodeFieldSpec] = Field(default_factory=list)
