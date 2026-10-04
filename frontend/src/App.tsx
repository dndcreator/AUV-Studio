import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import {
  addEdge,
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
  applyEdgeChanges,
  applyNodeChanges
} from "reactflow";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "./api";
import { buildGlimpseRows, type GlimpseRow } from "./glimpse";
import { Topbar } from "./components/Topbar";
import { StudioDock } from "./components/StudioDock";
import { StudioStatusRail } from "./components/StudioStatusRail";
import { EntityPalette } from "./components/EntityPalette";
import { ModelConfigModal } from "./components/ModelConfigModal";
import { MediaStudio } from "./components/MediaStudio";
import { ProjectLibraryModal } from "./components/ProjectLibraryModal";
import { normalizeModelProvider } from "./modelProviders";
import { defaultEdgeInteraction, useSelectedStore, useWorkflowStore } from "./store";
import { isEntityType, type EntityType } from "./simulationEntities";
import type { Lang, RoleCard, UiMode } from "./appTypes";
import {
  EdgeInteractionPanel,
  EnvironmentPanel,
  MetricCard,
  NodeConfigPanel,
  buildMonitorRows,
  buildMonitorStats,
  buildRelationshipRows,
  buildSimulationView,
  decisionActionToText,
  findRoleCardForNode,
  formatMs,
  formatRatio,
  formatDynamicStateValue,
  getModeVisibility,
  parseRoleCards,
  parseSimulationSemantics,
  statusClass,
  toBroadcastLine
} from "./appSupport";
import type {
  AuditVerify,
  DirectorCapability,
  DirectorCommandResponse,
  EvidencePack,
  FullLog,
  HumanResponseRequest,
  MemoryItem,
  ModelConfig,
  ModelProviderKind,
  ModelConfigTestResponse,
  NodeSpec,
  NodeType,
  PlanCompileResponse,
  ObservabilitySnapshot,
  QueueStatus,
  RunCompare,
  RunCheckpoint,
  RunEvent,
  RunMetrics,
  RunReport,
  TraceEventContext,
  TemplateSummary,
  WorkflowDefinition,
  WorkflowSummary
} from "./types";

export default function App() {
  const {
    workflow,
    nodes,
    edges,
    setNodes,
    setEdges,
    addNode,
    addEntityNode,
    addAgentPreset,
    setWorkflowMeta,
    setEnvironmentField,
    updateNodeConfig,
    updateNodeInput,
    updateEdgeInteraction,
    buildWorkflowPayload
  } = useWorkflowStore();
  const { selectedNodeId, selectedEdgeId, setSelectedNodeId, setSelectedEdgeId } = useSelectedStore();
  const { t, i18n } = useTranslation();
  const lang = (i18n.language?.startsWith("en") ? "en-US" : "zh-CN") as Lang;
  const setLang = (nextLang: Lang) => {
    void i18n.changeLanguage(nextLang);
  };
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [afterSeq, setAfterSeq] = useState(0);
  const [runStatus, setRunStatus] = useState<string>("idle");
  const [humanResponseText, setHumanResponseText] = useState("");
  const [humanResponder, setHumanResponder] = useState("user");
  const [directorText, setDirectorText] = useState("");
  const [directorScope, setDirectorScope] = useState<"global" | "phase" | "node">("global");
  const [directorTargetNode, setDirectorTargetNode] = useState("");
  const [directorResult, setDirectorResult] = useState<DirectorCommandResponse | null>(null);
  const [directorCapabilities, setDirectorCapabilities] = useState<DirectorCapability[]>([]);
  const [metrics, setMetrics] = useState<RunMetrics | null>(null);
  const [compareRunId, setCompareRunId] = useState("");
  const [compareResult, setCompareResult] = useState<RunCompare | null>(null);
  const [timelineSeq, setTimelineSeq] = useState(0);
  const [isTimelinePlaying, setIsTimelinePlaying] = useState(false);
  const [glimpseSnapshot, setGlimpseSnapshot] = useState<GlimpseRow[]>([]);
  const glimpseCursorRef = useRef(0);
  const glimpseRowsRef = useRef<GlimpseRow[]>([]);
  const completedRunShownRef = useRef<string | null>(null);
  const [reportMode, setReportMode] = useState<"briefing" | "narrative">("briefing");
  const [reportLength, setReportLength] = useState<"short" | "medium" | "long">("medium");
  const [reportTitle, setReportTitle] = useState("");
  const [reportAudience, setReportAudience] = useState("");
  const [reportStylePrompt, setReportStylePrompt] = useState("");
  const [reportResult, setReportResult] = useState<RunReport | null>(null);
  const [fullLogFormat, setFullLogFormat] = useState<"markdown" | "json">("markdown");
  const [fullLogResult, setFullLogResult] = useState<FullLog | null>(null);
  const [runInput, setRunInput] = useState("");
  const [uiNotice, setUiNotice] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [observability, setObservability] = useState<ObservabilitySnapshot | null>(null);
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null);
  const [auditVerify, setAuditVerify] = useState<AuditVerify | null>(null);
  const [traceContext, setTraceContext] = useState<TraceEventContext | null>(null);
  const [traceSeq, setTraceSeq] = useState<number | null>(null);
  const [traceError, setTraceError] = useState("");
  const [runCheckpoints, setRunCheckpoints] = useState<RunCheckpoint[]>([]);
  const [rollbackReason, setRollbackReason] = useState("");
  const [monitorMode, setMonitorMode] = useState<"fine" | "balanced" | "coarse">("balanced");
  const [monitorNodeFilter, setMonitorNodeFilter] = useState("all");
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [showModelConfig, setShowModelConfig] = useState(false);
  const [showLiveTranscript, setShowLiveTranscript] = useState(false);
  const [showRunResults, setShowRunResults] = useState(false);
  const [activeStudioPanel, setActiveStudioPanel] = useState<"create" | "inspect" | "advanced" | null>(null);
  const [createStep, setCreateStep] = useState<"auto" | "manual" | "templates">("auto");
  const [showDirectorBubble, setShowDirectorBubble] = useState(false);
  const [directorPosition, setDirectorPosition] = useState(() => {
    try {
      const raw = window.localStorage.getItem("auv.director.position");
      if (raw) {
        const parsed = JSON.parse(raw) as { x?: unknown; y?: unknown };
        if (typeof parsed.x === "number" && typeof parsed.y === "number") {
          return { x: parsed.x, y: parsed.y };
        }
      }
    } catch {
      // localStorage is optional for the draggable mascot.
    }
    return { x: 28, y: 380 };
  });
  const [modelConfig, setModelConfig] = useState<ModelConfig | null>(null);
  const [modelProvider, setModelProvider] = useState<ModelProviderKind>("openai_compatible");
  const [modelBaseUrl, setModelBaseUrl] = useState("https://api.openai.com/v1");
  const [modelDefaultModel, setModelDefaultModel] = useState("gpt-4o-mini");
  const [modelApiKey, setModelApiKey] = useState("");
  const [modelTestResult, setModelTestResult] = useState<ModelConfigTestResponse | null>(null);
  const [planText, setPlanText] = useState("");
  const [planMode, setPlanMode] = useState<"auto" | "research" | "roleplay" | "custom" | "collab">("auto");
  const [planSubmode, setPlanSubmode] = useState<"auto" | "simulation" | "research" | "consulting">("auto");
  const [planMaxAgents, setPlanMaxAgents] = useState(6);
  const [planBlueprint, setPlanBlueprint] = useState<Record<string, unknown> | null>(null);
  const [planDraft, setPlanDraft] = useState<PlanCompileResponse | null>(null);
  const [evidencePack, setEvidencePack] = useState<EvidencePack | null>(null);
  const [showEvidenceModal, setShowEvidenceModal] = useState(false);
  const [showOpenProject, setShowOpenProject] = useState(false);
  const [projects, setProjects] = useState<WorkflowSummary[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [projectSearch, setProjectSearch] = useState("");
  const [evidenceReviewed, setEvidenceReviewed] = useState(false);
  const [isApplyingPlanDraft, setIsApplyingPlanDraft] = useState(false);
  const [templateName, setTemplateName] = useState("");
  const [templateCategory, setTemplateCategory] = useState("general");
  const [templateTags, setTemplateTags] = useState("");
  const [templateDescription, setTemplateDescription] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [nodeSpecs, setNodeSpecs] = useState<Record<NodeType, NodeSpec>>({} as Record<NodeType, NodeSpec>);
  const [interactionModes, setInteractionModes] = useState<string[]>(["report", "instruction", "feedback", "dialogue", "handoff"]);
  const [newRoleName, setNewRoleName] = useState("");
  const [newRoleArchetype, setNewRoleArchetype] = useState("");
  const [newRoleGoal, setNewRoleGoal] = useState("");
  const [newRoleStyle, setNewRoleStyle] = useState("");
  const [newRoleBoundaries, setNewRoleBoundaries] = useState("");
  const [roleplayNegativePrompts, setRoleplayNegativePrompts] = useState("");
  const [broadcastTone] = useState<"news" | "warroom" | "calm">("news");
  const [decisionAction, setDecisionAction] = useState<"continue" | "adjust_direction" | "reduce_cost" | "inject_event">("continue");
  const [hoverRoleCard, setHoverRoleCard] = useState<{
    nodeId: string;
    x: number;
    y: number;
    card: RoleCard;
  } | null>(null);
  const canvasRef = useRef<HTMLElement | null>(null);
  const directorDragRef = useRef({
    active: false,
    moved: false,
    pointerId: -1,
    startX: 0,
    startY: 0,
    originX: 0,
    originY: 0
  });
  const tr = t;
  const copy = (zh: string, en: string) => (lang === "en-US" ? en : zh);
  const selectedNode = useMemo(() => nodes.find((n) => n.id === selectedNodeId) ?? null, [nodes, selectedNodeId]);
  const selectedEdge = useMemo(() => edges.find((e) => e.id === selectedEdgeId) ?? null, [edges, selectedEdgeId]);
  const selectedNodeType = (String((selectedNode?.data as Record<string, unknown> | undefined)?.nodeType ?? "prompt") ||
    "prompt") as NodeType;
  const selectedSpec = nodeSpecs[selectedNodeType];
  const maxSeq = useMemo(() => events.reduce((m, e) => Math.max(m, Number(e.seq ?? 0)), 0), [events]);
  const eventCount = events.length;
  const entityCounts = useMemo(() => {
    const counts: Partial<Record<EntityType, number>> = {};
    for (const node of nodes) {
      const config = ((node.data as Record<string, unknown>)?.config ?? {}) as Record<string, unknown>;
      if (isEntityType(config.entity_type)) {
        counts[config.entity_type] = (counts[config.entity_type] ?? 0) + 1;
      }
    }
    return counts;
  }, [nodes]);
  const entityCount = useMemo(() => Object.values(entityCounts).reduce((sum, count) => sum + (count ?? 0), 0), [entityCounts]);
  const backgroundCount = workflow.environment.context_book?.entries?.filter((entry) => entry.enabled).length ?? 0;
  const visibleEvents = useMemo(() => {
    if (timelineSeq <= 0) {
      return events;
    }
    return events.filter((e) => Number(e.seq ?? 0) <= timelineSeq);
  }, [events, timelineSeq]);
  const simulationMeta = useMemo(
    () => ((workflow.environment?.simulation as Record<string, unknown> | undefined) ?? {}) as Record<string, unknown>,
    [workflow.environment]
  );
  const uiMode = (String(simulationMeta.ui_mode ?? "research") || "research") as UiMode;
  const monitorRows = useMemo(() => buildMonitorRows(visibleEvents, monitorMode, monitorNodeFilter), [visibleEvents, monitorMode, monitorNodeFilter]);
  const monitorStats = useMemo(() => buildMonitorStats(visibleEvents), [visibleEvents]);
  const glimpseRows = useMemo(() => {
    const labels = new Map(
      nodes.map((node) => {
        const data = (node.data as Record<string, unknown> | undefined) ?? {};
        const config = (data.config as Record<string, unknown> | undefined) ?? {};
        const fallback = node.id.startsWith("summary_") ? copy("旁白", "Narrator") : String(data.label ?? node.id);
        return [node.id, String(config.entity_name ?? config.profile ?? fallback)] as const;
      })
    );
    return buildGlimpseRows(visibleEvents).map((row) => ({ ...row, speaker: labels.get(row.speaker) ?? row.speaker }));
  }, [visibleEvents, nodes, lang]);
  const currentGlimpse = useMemo(() => {
    if (glimpseSnapshot.length === 0) {
      return null;
    }
    return glimpseSnapshot[glimpseSnapshot.length - 1] ?? null;
  }, [glimpseSnapshot]);
  const broadcastRows = useMemo(() => {
    return glimpseSnapshot
      .slice(-16)
      .map((row) => ({
        ...row,
        line: toBroadcastLine(row, uiMode, broadcastTone)
      }))
      .filter((row) => row.line.trim().length > 0)
      .reverse();
  }, [glimpseSnapshot, uiMode, broadcastTone]);
  const finalResultText = useMemo(() => {
    const completed = [...events]
      .reverse()
      .find((event) => event.event === "succeeded" && event.node_id.startsWith("summary_"));
    const payload = (completed?.payload ?? {}) as Record<string, unknown>;
    const output = (payload.output ?? {}) as Record<string, unknown>;
    return String(output.content ?? output.text ?? output.summary ?? "").trim();
  }, [events]);
  const simulationView = useMemo(() => buildSimulationView(visibleEvents), [visibleEvents]);
  const waitingEvent = useMemo(() => {
    const rows = [...events].reverse();
    return rows.find((e) => e.event === "waiting_human") ?? null;
  }, [events]);
  const waitingNodeId = waitingEvent?.node_id ?? "";
  const monitorNodeOptions = useMemo(() => {
    const set = new Set<string>();
    for (const e of visibleEvents) {
      set.add(e.node_id);
    }
    return Array.from(set).sort();
  }, [visibleEvents]);
  const roleCards = useMemo(() => parseRoleCards(simulationMeta.role_cards), [simulationMeta]);
  const modeVisibility = useMemo(() => getModeVisibility(uiMode), [uiMode]);
  const simulationSeed = String(simulationMeta.seed ?? "");
  const detailGranularity = String(simulationMeta.detail_granularity ?? "concise");
  const simulationSemantics = parseSimulationSemantics(simulationMeta.semantics);
  const relationshipRows = useMemo(() => buildRelationshipRows(edges), [edges]);
  const preflightConfig = useMemo(() => {
    const raw = simulationMeta.preflight_confirm as Record<string, unknown> | undefined;
    return {
      enabled: Boolean(raw?.enabled ?? false),
      warmupNodes: Number(raw?.warmup_nodes ?? 1) || 1,
      minRemainingExpensiveNodes: Number(raw?.min_remaining_expensive_nodes ?? 1) || 1,
      question: String(raw?.question ?? "请确认当前方向是否正确，再继续高成本执行。")
    };
  }, [simulationMeta]);
  const draftNeedsRegeneration = useMemo(() => {
    if (!planDraft) {
      return false;
    }
    const generatedMode = String(planDraft.simulation_blueprint?.mode ?? "custom");
    const generatedSubmode = String(planDraft.simulation_blueprint?.submode ?? "none");
    return generatedMode !== planMode || (planMode === "research" && generatedSubmode !== planSubmode);
  }, [planDraft, planMode, planSubmode]);

  useEffect(() => {
    api
      .getNodeSpecs()
      .then((specs) => {
        const map = specs.reduce<Record<string, NodeSpec>>((acc, s) => {
          acc[s.type] = s;
          return acc;
        }, {});
        setNodeSpecs(map as Record<NodeType, NodeSpec>);
      })
      .catch(() => {
        setNodeSpecs({} as Record<NodeType, NodeSpec>);
      });
    api
      .getInteractionModes()
      .then((modes) => setInteractionModes(modes))
      .catch(() => setInteractionModes(["report", "instruction", "feedback", "dialogue", "handoff"]));
    api
      .getModelConfig()
      .then((config) => {
        setModelConfig(config);
        setModelProvider(normalizeModelProvider(config.provider));
        setModelBaseUrl(config.base_url);
        setModelDefaultModel(config.default_model);
      })
      .catch(() => setModelConfig(null));
    api
      .getDirectorCapabilities()
      .then((caps) => setDirectorCapabilities(caps))
      .catch(() => setDirectorCapabilities([]));
    refreshTemplates();
    api
      .getObservability()
      .then((snap) => setObservability(snap))
      .catch(() => setObservability(null));
    api
      .getQueueStatus()
      .then((s) => setQueueStatus(s))
      .catch(() => setQueueStatus(null));
    api
      .getMemories(workflow.id, 80)
      .then((rows) => setMemories(rows))
      .catch(() => setMemories([]));
  }, []);

  useEffect(() => {
    const rows = simulationMeta.roleplay_boundaries;
    if (!Array.isArray(rows)) {
      setRoleplayNegativePrompts("");
      return;
    }
    setRoleplayNegativePrompts(rows.map((v) => String(v)).join("\n"));
  }, [simulationMeta]);

  useEffect(() => {
    if (!uiNotice) {
      return;
    }
    const timer = window.setTimeout(() => setUiNotice(null), uiNotice.type === "error" ? 7000 : 3200);
    return () => window.clearTimeout(timer);
  }, [uiNotice]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      const payload = buildWorkflowPayload();
      try {
        return await api.updateWorkflow(payload);
      } catch {
        return await api.createWorkflow(payload);
      }
    },
    onSuccess: () => setUiNotice({ type: "success", message: copy("已保存", "Saved") }),
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("保存失败", "Save failed") })
  });

  const runMutation = useMutation({
    mutationFn: async () => {
      const payload = buildWorkflowPayload();
      try {
        await api.updateWorkflow(payload);
      } catch {
        await api.createWorkflow(payload);
      }
      return api.runWorkflow(payload.id, { task: runInput.trim() || payload.environment.scenario || payload.name });
    },
    onSuccess: (result) => {
      setUiNotice({ type: "success", message: copy("运行已启动", "Run started") });
      setRunId(result.run_id);
      setRunStatus(result.status);
      setEvents([]);
      setAfterSeq(0);
      setTimelineSeq(0);
      setMetrics(null);
      setCompareResult(null);
      setReportResult(null);
      setFullLogResult(null);
      setRunCheckpoints([]);
      setGlimpseSnapshot([]);
      glimpseCursorRef.current = 0;
      glimpseRowsRef.current = [];
      setTraceContext(null);
      setTraceSeq(null);
      setTraceError("");
      setAuditVerify(null);
      setShowRunResults(false);
    },
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("启动失败", "Run failed to start") })
  });

  const stopMutation = useMutation({
    mutationFn: async () => {
      if (!runId) {
        throw new Error("run_id is empty");
      }
      return api.stopRun(runId);
    },
    onSuccess: (data) => {
      setRunStatus(data.status);
      setUiNotice({
        type: "success",
        message: data.status === "stopping"
          ? copy("停止请求已发送，当前响应完成后停止", "Stop requested. The run will stop after the current response.")
          : copy("运行已停止", "Run stopped")
      });
    },
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("停止失败", "Failed to stop run") })
  });

  const compareMutation = useMutation({
    mutationFn: async (params: { runA: string; runB: string }) => api.compareRuns(params.runA, params.runB),
    onSuccess: (data) => setCompareResult(data),
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("对比失败", "Compare failed") })
  });

  const reportMutation = useMutation({
    mutationFn: async (params: { run: string; mode?: "briefing" | "narrative" }) =>
      api.generateReport(params.run, {
        mode: params.mode ?? reportMode,
        length: reportLength,
        title: reportTitle.trim() || undefined,
        audience: reportAudience.trim() || undefined,
        style_prompt: reportStylePrompt.trim() || undefined
      }),
    onSuccess: (data) => setReportResult(data),
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("报告生成失败", "Report generation failed") })
  });

  const fullLogMutation = useMutation({
    mutationFn: async (run: string) => api.getFullLog(run, fullLogFormat),
    onSuccess: (data) => setFullLogResult(data),
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("日志加载失败", "Full log loading failed") })
  });

  const checkpointsMutation = useMutation({
    mutationFn: async (run: string) => api.getRunCheckpoints(run, 80),
    onSuccess: (data) => setRunCheckpoints(data.checkpoints),
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("检查点加载失败", "Checkpoint loading failed") })
  });

  const rollbackMutation = useMutation({
    mutationFn: async (checkpoint: RunCheckpoint) => {
      if (!runId) {
        throw new Error("run_id is empty");
      }
      return api.rollbackRun(runId, checkpoint.seq, rollbackReason.trim() || undefined);
    },
    onSuccess: (data) => {
      setRunId(data.run_id);
      setRunStatus(data.status);
      setEvents([]);
      setAfterSeq(0);
      setTimelineSeq(0);
      setMetrics(null);
      setCompareResult(null);
      setReportResult(null);
      setFullLogResult(null);
      setRunCheckpoints([]);
      setRollbackReason("");
      setTraceContext(null);
      setTraceSeq(null);
      setTraceError("");
      setUiNotice({ type: "success", message: copy("已从检查点重新开始", "Restarted from checkpoint") });
    },
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("回滚失败", "Rollback failed") })
  });

  const humanResponseMutation = useMutation({
    mutationFn: async (payload: HumanResponseRequest) => {
      if (!runId) {
        throw new Error("run_id is empty");
      }
      return api.respondHumanCheckpoint(runId, payload);
    },
    onSuccess: (data) => {
      setRunStatus(data.status);
      setHumanResponseText("");
      if (runId) {
        pollEvents.mutate(runId);
      }
      setUiNotice({ type: "success", message: copy("已继续运行", "Run resumed") });
    },
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("人工响应提交失败", "Human response failed") })
  });

  const directorCommandMutation = useMutation({
    mutationFn: async () => {
      if (!runId) {
        throw new Error("run_id is empty");
      }
      return api.sendDirectorCommand(runId, {
        text: directorText.trim(),
        scope: directorScope,
        target_node_id: directorScope === "node" ? directorTargetNode.trim() || undefined : undefined,
        strict: false
      });
    },
    onSuccess: (data) => {
      setDirectorResult(data);
      setDirectorText("");
      if (runId) {
        pollEvents.mutate(runId);
      }
      setUiNotice({ type: "success", message: copy("导演指令已应用", "Director command applied") });
    },
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("导演指令失败", "Director command failed") })
  });

  const saveTemplateMutation = useMutation({
    mutationFn: async () => {
      const payload = buildWorkflowPayload();
      return api.createTemplate({
        id: `tpl_${Date.now().toString(36)}`,
        name: templateName.trim() || `${payload.name} Template`,
        description: templateDescription.trim(),
        category: templateCategory.trim() || "general",
        tags: templateTags
          .split(",")
          .map((t) => t.trim())
          .filter((t) => t.length > 0),
        workflow: payload
      });
    },
    onSuccess: () => {
      refreshTemplates();
      setTemplateName("");
      setTemplateDescription("");
      setTemplateTags("");
      setUiNotice({ type: "success", message: copy("模板已保存", "Template saved") });
    },
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("模板保存失败", "Template save failed") })
  });

  const clearMemoryMutation = useMutation({
    mutationFn: async () => api.clearMemories(workflow.id),
    onSuccess: () => setMemories([]),
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("记忆清理失败", "Memory clearing failed") })
  });

  const saveModelConfigMutation = useMutation({
    mutationFn: async () =>
      api.saveModelConfig({
        provider: modelProvider,
        base_url: modelBaseUrl.trim(),
        default_model: modelDefaultModel.trim(),
        api_key: modelApiKey.trim() || undefined
      }),
    onSuccess: (config) => {
      setModelConfig(config);
      setModelProvider(normalizeModelProvider(config.provider));
      setModelBaseUrl(config.base_url);
      setModelDefaultModel(config.default_model);
      setModelApiKey("");
      setModelTestResult(null);
      setUiNotice({ type: "success", message: copy("模型配置已保存", "Model config saved") });
    },
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("模型配置保存失败", "Model config save failed") })
  });

  const testModelConfigMutation = useMutation({
    mutationFn: async () =>
      api.testModelConfig({
        provider: modelProvider,
        base_url: modelBaseUrl.trim(),
        default_model: modelDefaultModel.trim(),
        api_key: modelApiKey.trim() || undefined
      }),
    onSuccess: (result) => setModelTestResult(result),
    onError: (err) =>
      setModelTestResult({
        ok: false,
        message: err instanceof Error ? err.message : "Connection test failed.",
        latency_ms: null
      })
  });

  const acceptPlanCompileResponse = (data: PlanCompileResponse) => {
    setPlanDraft(data);
    setPlanBlueprint(data.simulation_blueprint ?? ((data.workflow.environment?.simulation as Record<string, unknown>) ?? null));
    const detectedMode = String(data.simulation_blueprint?.mode ?? "custom");
    setPlanMode((detectedMode === "research" || detectedMode === "roleplay" || detectedMode === "custom") ? detectedMode : "custom");
    const detectedSubmode = String(data.simulation_blueprint?.submode ?? "auto");
    setPlanSubmode(
      detectedSubmode === "simulation" || detectedSubmode === "research" || detectedSubmode === "consulting" ? detectedSubmode : "auto"
    );
    if (detectedMode === "research" && !evidenceReviewed) {
      setShowEvidenceModal(true);
    }
  };

  const evidenceMutation = useMutation({
    mutationFn: (file: File) => api.parseEvidence(file),
    onSuccess: (pack) => {
      setEvidencePack(pack);
      setUiNotice({ type: "success", message: copy("证据包解析完成", "Evidence package parsed") });
    },
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("证据包解析失败", "Evidence parsing failed") })
  });

  const compilePlanMutation = useMutation({
    mutationFn: async () =>
      api.compileTemplate({
        plan_text: planText.trim(),
        mode: "auto",
        max_agents: 6,
        language: lang === "en-US" ? "en" : "zh"
      }),
    onSuccess: (data) => {
      acceptPlanCompileResponse(data);
    },
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("计划解析失败", "Plan compilation failed") })
  });

  const directorArrangeMutation = useMutation({
    mutationFn: async () =>
      api.compileTemplate({
        plan_text: directorText.trim(),
        mode: "auto",
        max_agents: Math.max(2, Math.min(12, Number(planMaxAgents) || 6)),
        language: lang === "en-US" ? "en" : "zh"
      }),
    onSuccess: (data) => {
      setPlanText(directorText.trim());
      setDirectorText("");
      acceptPlanCompileResponse(data);
      setActiveStudioPanel("create");
      setCreateStep("auto");
      setShowDirectorBubble(false);
      setUiNotice({
        type: "success",
        message: copy("导演已生成草案，请确认后应用", "Director draft is ready. Review and apply it.")
      });
    },
    onError: (err) => setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("导演安排失败", "Director arrangement failed") })
  });

  const pollEvents = useMutation({
    mutationFn: async (currentRunId: string) => api.getRunEvents(currentRunId, afterSeq),
    onSuccess: async (data) => {
      if (data.events.length > 0) {
        setEvents((prev) => [...prev, ...data.events]);
        const maxSeq = data.events[data.events.length - 1].seq ?? afterSeq;
        setAfterSeq(maxSeq);
      }
      if (runId) {
        const detail = await api.getRun(runId);
        setRunStatus(detail.status);
        const m = await api.getRunMetrics(runId);
        setMetrics(m);
        if (data.events.length > 0) {
          const checkpoints = await api.getRunCheckpoints(runId, 80);
          setRunCheckpoints(checkpoints.checkpoints);
        }
      }
    }
  });

  useEffect(() => {
    if (runId) {
      api.getRunCheckpoints(runId, 80).then((data) => setRunCheckpoints(data.checkpoints)).catch(() => setRunCheckpoints([]));
    } else {
      setRunCheckpoints([]);
    }
  }, [runId]);

  useEffect(() => {
    const timer = setInterval(() => {
      if (runId && (runStatus === "queued" || runStatus === "pending" || runStatus === "running" || runStatus === "stopping")) {
        pollEvents.mutate(runId);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [pollEvents, runId, runStatus]);

  useEffect(() => {
    if (maxSeq > 0 && !isTimelinePlaying) {
      setTimelineSeq(maxSeq);
    }
  }, [maxSeq, isTimelinePlaying]);

  useEffect(() => {
    if (runStatus !== "succeeded" || !runId || completedRunShownRef.current === runId) {
      return;
    }
    completedRunShownRef.current = runId;
    setShowRunResults(true);
  }, [runId, runStatus]);

  useEffect(() => {
    if (!isTimelinePlaying || maxSeq <= 0) {
      return;
    }
    const sortedSeq = Array.from(new Set(events.map((e) => Number(e.seq ?? 0)).filter((n) => n > 0))).sort((a, b) => a - b);
    const timer = setInterval(() => {
      setTimelineSeq((cur) => {
        const idx = sortedSeq.findIndex((s) => s > cur);
        if (idx === -1) {
          setIsTimelinePlaying(false);
          return maxSeq;
        }
        return sortedSeq[idx];
      });
    }, 600);
    return () => clearInterval(timer);
  }, [events, isTimelinePlaying, maxSeq]);

  useEffect(() => {
    if (glimpseRows.length < glimpseCursorRef.current) {
      glimpseCursorRef.current = 0;
    }
    glimpseRowsRef.current = glimpseRows;
  }, [glimpseRows]);

  useEffect(() => {
    const timer = setInterval(() => {
      const rows = glimpseRowsRef.current;
      const next = rows[glimpseCursorRef.current];
      if (!next) return;
      glimpseCursorRef.current += 1;
      setGlimpseSnapshot((prev) => [...prev, next].slice(-30));
    }, 6000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const timer = setInterval(() => {
      api
        .getObservability()
        .then((snap) => setObservability(snap))
        .catch(() => {
          // ignore transient errors in dashboard polling
        });
      api
        .getQueueStatus()
        .then((s) => setQueueStatus(s))
        .catch(() => {
          // ignore transient errors in dashboard polling
        });
      api
        .getMemories(workflow.id, 80)
        .then((rows) => setMemories(rows))
        .catch(() => {
          // ignore transient errors in dashboard polling
        });
    }, 5000);
    return () => clearInterval(timer);
  }, [workflow.id]);

  useEffect(() => {
    if (!runId) {
      setAuditVerify(null);
      return;
    }
    const refreshAudit = () => {
      api
        .verifyRunAudit(runId)
        .then((v) => setAuditVerify(v))
        .catch(() => {
          // ignore transient errors in audit polling
        });
    };
    refreshAudit();
    const timer = setInterval(refreshAudit, 8000);
    return () => clearInterval(timer);
  }, [runId]);

  const inspectTrace = async (seq: number) => {
    if (!runId) {
      return;
    }
    setTraceError("");
    setTraceSeq(seq);
    try {
      const detail = await api.getRunTraceEvent(runId, seq);
      setTraceContext(detail);
    } catch (err) {
      setTraceContext(null);
      setTraceError(err instanceof Error ? err.message : "Trace lookup failed");
    }
  };

  const clampDirectorPosition = (position: { x: number; y: number }) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) {
      return position;
    }
    const maxX = Math.max(12, rect.width - 140);
    const maxY = Math.max(12, rect.height - 150);
    return {
      x: Math.min(Math.max(position.x, 12), maxX),
      y: Math.min(Math.max(position.y, 12), maxY)
    };
  };

  const persistDirectorPosition = (position: { x: number; y: number }) => {
    try {
      window.localStorage.setItem("auv.director.position", JSON.stringify(position));
    } catch {
      // Position persistence is non-critical.
    }
  };

  const onDirectorPointerDown = (event: PointerEvent<HTMLButtonElement>) => {
    directorDragRef.current = {
      active: true,
      moved: false,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: directorPosition.x,
      originY: directorPosition.y
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onDirectorPointerMove = (event: PointerEvent<HTMLButtonElement>) => {
    const drag = directorDragRef.current;
    if (!drag.active || drag.pointerId !== event.pointerId) {
      return;
    }
    const deltaX = event.clientX - drag.startX;
    const deltaY = event.clientY - drag.startY;
    if (Math.abs(deltaX) + Math.abs(deltaY) > 4) {
      drag.moved = true;
    }
    setDirectorPosition(clampDirectorPosition({ x: drag.originX + deltaX, y: drag.originY + deltaY }));
  };

  const onDirectorPointerUp = (event: PointerEvent<HTMLButtonElement>) => {
    const drag = directorDragRef.current;
    if (!drag.active || drag.pointerId !== event.pointerId) {
      return;
    }
    const next = clampDirectorPosition({
      x: drag.originX + event.clientX - drag.startX,
      y: drag.originY + event.clientY - drag.startY
    });
    directorDragRef.current = { ...drag, active: false };
    setDirectorPosition(next);
    persistDirectorPosition(next);
    if (!drag.moved) {
      setShowDirectorBubble((value) => !value);
    }
  };

  const onNodesChange = (changes: Parameters<typeof applyNodeChanges>[0]) =>
    setNodes(applyNodeChanges(changes, nodes as Node[]));
  const onEdgesChange = (changes: Parameters<typeof applyEdgeChanges>[0]) =>
    setEdges(applyEdgeChanges(changes, edges as Edge[]));
  const onConnect = (connection: Connection) =>
    setEdges(addEdge({ ...connection, id: `e_${Date.now()}`, data: { interaction: defaultEdgeInteraction() } }, edges));
  const hoverEnabled = nodes.length > 0 && nodes.length <= 12;

  const onExport = () => {
    const payload = buildWorkflowPayload();
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${payload.id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const updateSimulationMeta = (patch: Record<string, unknown>) => {
    setEnvironmentField("simulation", {
      ...simulationMeta,
      ...patch
    });
  };

  const onUiModeChange = (mode: UiMode) => {
    updateSimulationMeta({ ui_mode: mode });
  };

  const upsertRoleCard = (card: RoleCard) => {
    const next = [...roleCards.filter((r) => r.id !== card.id), card];
    updateSimulationMeta({ role_cards: next.sort((a, b) => a.name.localeCompare(b.name, "zh-CN")) });
  };

  const removeRoleCard = (id: string) => {
    updateSimulationMeta({ role_cards: roleCards.filter((r) => r.id !== id) });
  };

  const applyPlanDraft = async () => {
    if (!planDraft) {
      return;
    }
    setIsApplyingPlanDraft(true);
    try {
      const generatedMode = String(planDraft.simulation_blueprint?.mode ?? "custom");
      const generatedSubmode = String(planDraft.simulation_blueprint?.submode ?? "none");
      const modeChanged = generatedMode !== planMode || (planMode === "research" && generatedSubmode !== planSubmode);
      const draftEvidence = (planDraft.workflow.environment.simulation as Record<string, unknown> | undefined)?.evidence;
      const evidenceChanged = planMode === "research" && evidencePack !== null && !draftEvidence;
      if (modeChanged || evidenceChanged) {
        const refreshedDraft = await api.compileTemplate({
            plan_text: planText.trim(),
            mode: planMode,
            submode: planMode === "research" && planSubmode !== "auto" ? planSubmode : undefined,
            max_agents: 6,
            language: lang === "en-US" ? "en" : "zh",
            evidence_pack: evidencePack ?? undefined
          });
        acceptPlanCompileResponse(refreshedDraft);
        setUiNotice({ type: "success", message: copy("导演已按新模式更新方案，请再次确认", "Director updated the plan. Review it once more.") });
        return;
      }
      if (planMode === "research" && !evidenceReviewed) {
        setShowEvidenceModal(true);
        return;
      }
      useWorkflowStore.getState().loadWorkflow(planDraft.workflow);
      setPlanDraft(null);
      setPlanBlueprint(null);
      setActiveStudioPanel(null);
      setShowDirectorBubble(false);
      setUiNotice({ type: "success", message: copy("方案已应用，可以开始模拟", "Plan applied. The simulation is ready.") });
    } catch (error) {
      setUiNotice({ type: "error", message: error instanceof Error ? error.message : copy("应用方案失败", "Failed to apply plan") });
    } finally {
      setIsApplyingPlanDraft(false);
    }
  };

  const validateImportedWorkflow = (value: unknown): WorkflowDefinition => {
    if (!value || typeof value !== "object") {
      throw new Error(copy("导入内容不是有效对象", "Imported content is not an object"));
    }
    const workflowValue = value as Partial<WorkflowDefinition>;
    if (
      typeof workflowValue.id !== "string" ||
      typeof workflowValue.name !== "string" ||
      typeof workflowValue.version !== "number" ||
      !Array.isArray(workflowValue.nodes) ||
      !Array.isArray(workflowValue.edges) ||
      !Array.isArray(workflowValue.entry_nodes)
    ) {
      throw new Error(copy("导入文件缺少工作流核心字段", "Imported workflow is missing required fields"));
    }
    for (const node of workflowValue.nodes) {
      if (
        !node ||
        typeof node.id !== "string" ||
        typeof node.type !== "string" ||
        !node.position ||
        typeof node.position.x !== "number" ||
        typeof node.position.y !== "number" ||
        typeof node.config !== "object" ||
        typeof node.inputs !== "object"
      ) {
        throw new Error(copy("导入文件包含无效节点", "Imported workflow contains an invalid node"));
      }
    }
    for (const edge of workflowValue.edges) {
      if (!edge || typeof edge.id !== "string" || typeof edge.source !== "string" || typeof edge.target !== "string") {
        throw new Error(copy("导入文件包含无效连线", "Imported workflow contains an invalid edge"));
      }
    }
    return workflowValue as WorkflowDefinition;
  };

  const onImport = async (file: File) => {
    try {
      const text = await file.text();
      const parsed = validateImportedWorkflow(JSON.parse(text));
      useWorkflowStore.getState().loadWorkflow(parsed);
      setUiNotice({ type: "success", message: copy("工作流已导入", "Workflow imported") });
    } catch (err) {
      setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("导入失败", "Import failed") });
    }
  };

  const refreshTemplates = () => {
    api
      .listTemplates()
      .then((rows) => setTemplates(rows))
      .catch(() => setTemplates([]));
  };

  const applyTemplate = async (templateId: string) => {
    try {
      const detail = await api.getTemplate(templateId);
      useWorkflowStore.getState().loadWorkflow(detail.workflow);
      setUiNotice({ type: "success", message: copy("模板已加载", "Template loaded") });
    } catch (err) {
      setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("模板加载失败", "Template loading failed") });
    }
  };

  const openProjectLibrary = async () => {
    setShowOpenProject(true);
    setProjectsLoading(true);
    try {
      setProjects(await api.listWorkflows());
    } catch (err) {
      setProjects([]);
      setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("项目列表加载失败", "Failed to load projects") });
    } finally {
      setProjectsLoading(false);
    }
  };

  const openProject = async (projectId: string) => {
    try {
      const project = await api.getWorkflow(projectId);
      useWorkflowStore.getState().loadWorkflow(project);
      setRunId(null);
      setRunStatus("idle");
      setEvents([]);
      setAfterSeq(0);
      setShowOpenProject(false);
      setProjectSearch("");
      setUiNotice({ type: "success", message: copy(`已打开项目：${project.name}`, `Opened project: ${project.name}`) });
    } catch (err) {
      setUiNotice({ type: "error", message: err instanceof Error ? err.message : copy("项目打开失败", "Failed to open project") });
    }
  };

  const visibleProjects = projects.filter((project) => {
    const query = projectSearch.trim().toLowerCase();
    return !query || project.name.toLowerCase().includes(query) || project.id.toLowerCase().includes(query);
  });

  return (
    <div className={`page theme-${uiMode}`}>
      <Topbar
        lang={lang}
        workflowName={workflow.name}
        isSaving={saveMutation.isPending}
        isStarting={runMutation.isPending}
        isRunning={["queued", "pending", "running"].includes(runStatus)}
        isStopping={runStatus === "stopping" || stopMutation.isPending}
        onLangChange={setLang}
        onWorkflowNameChange={setWorkflowMeta}
        onSave={() => saveMutation.mutate()}
        onOpenProject={() => void openProjectLibrary()}
        onRun={() => runMutation.mutate()}
        onStop={() => stopMutation.mutate()}
        onExport={onExport}
        onImport={onImport}
      />

      {uiNotice ? (
        <div className={`ui-notice ${uiNotice.type}`} role="status">
          <span>{uiNotice.message}</span>
          <button className="notice-close" onClick={() => setUiNotice(null)} aria-label="close-notice">
            ×
          </button>
        </div>
      ) : null}

      <ProjectLibraryModal
        open={showOpenProject}
        projects={visibleProjects}
        loading={projectsLoading}
        search={projectSearch}
        lang={lang}
        copy={copy}
        onSearchChange={setProjectSearch}
        onOpen={(id) => void openProject(id)}
        onClose={() => setShowOpenProject(false)}
      />

      <ModelConfigModal
        open={showModelConfig}
        config={modelConfig}
        provider={modelProvider}
        baseUrl={modelBaseUrl}
        defaultModel={modelDefaultModel}
        apiKey={modelApiKey}
        testResult={modelTestResult}
        saving={saveModelConfigMutation.isPending}
        testing={testModelConfigMutation.isPending}
        copy={copy}
        onClose={() => setShowModelConfig(false)}
        onProviderChange={(provider, baseUrl, model) => {
          setModelProvider(provider);
          setModelBaseUrl(baseUrl);
          setModelDefaultModel(model);
          setModelApiKey("");
          setModelTestResult(null);
        }}
        onBaseUrlChange={setModelBaseUrl}
        onDefaultModelChange={setModelDefaultModel}
        onApiKeyChange={setModelApiKey}
        onSave={() => saveModelConfigMutation.mutate()}
        onTest={() => testModelConfigMutation.mutate()}
      />
      {showEvidenceModal ? (
        <div className="modal-backdrop evidence-backdrop" role="dialog" aria-modal="true">
          <div className="modal-card evidence-card">
            <div className="modal-head">
              <div>
                <span className="result-kicker">RESEARCH EVIDENCE</span>
                <h2>{copy("添加研究证据", "Add Research Evidence")}</h2>
              </div>
            </div>
            <label className="evidence-dropzone">
              <strong>{evidenceMutation.isPending ? copy("正在本地解析...", "Parsing locally...") : copy("选择证据包或文件", "Choose evidence package or file")}</strong>
              <span>ZIP · XLSX · DOCX · CSV · JSON · TXT · MD</span>
              <input
                type="file"
                accept=".zip,.xlsx,.docx,.csv,.json,.txt,.md"
                disabled={evidenceMutation.isPending}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) evidenceMutation.mutate(file);
                  event.currentTarget.value = "";
                }}
              />
            </label>
            {evidencePack ? (
              <div className="evidence-review">
                <div className="draft-contract">
                  <span>{copy("已解析", "Parsed")}: {evidencePack.summary.parsed_files}</span>
                  <span>{copy("跳过", "Skipped")}: {evidencePack.summary.skipped_files}</span>
                  <span>{copy("失败", "Failed")}: {evidencePack.summary.failed_files}</span>
                </div>
                <div className="evidence-file-list">
                  {evidencePack.items.map((item) => (
                    <div className="evidence-file ok" key={item.id}><strong>{item.name}</strong><span>{item.kind}</span></div>
                  ))}
                  {[...evidencePack.skipped, ...evidencePack.errors].map((item, index) => (
                    <div className="evidence-file skipped" key={`${item.name}-${index}`}><strong>{item.name}</strong><span>{item.reason}</span></div>
                  ))}
                </div>
              </div>
            ) : null}
            <div className="result-actions">
              <button
                className="btn primary"
                disabled={!evidencePack || evidencePack.items.length === 0 || evidenceMutation.isPending}
                onClick={() => {
                  setEvidenceReviewed(true);
                  setShowEvidenceModal(false);
                  setUiNotice({ type: "success", message: copy("证据将在应用方案时交给导演重新解析", "Evidence will be applied when the Director refreshes the plan") });
                }}
              >
                {copy("确认使用", "Use Evidence")}
              </button>
              <button
                className="btn"
                onClick={() => {
                  setEvidencePack(null);
                  setEvidenceReviewed(true);
                  setShowEvidenceModal(false);
                  setUiNotice({ type: "success", message: copy("将基于用户描述和模型先验进行模拟", "The simulation will use the prompt and model priors") });
                }}
              >
                {copy("暂不提供", "Continue Without Evidence")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {showLiveTranscript ? (
        <div className="modal-backdrop live-transcript-backdrop" role="dialog" aria-modal="true" onClick={() => setShowLiveTranscript(false)}>
          <div className="modal-card live-transcript-card" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <div>
                <h2>{copy("全量实况", "Full Live Scene")}</h2>
                <span>{copy(`${glimpseRows.length} 条实际互动`, `${glimpseRows.length} actual interactions`)}</span>
              </div>
              <button className="btn" onClick={() => setShowLiveTranscript(false)}>{copy("关闭", "Close")}</button>
            </div>
            <div className="live-transcript-list">
              <div className="live-transcript-section-title">{copy("实际互动", "Actual Interactions")}</div>
              {glimpseRows.length === 0 ? <div className="live-window-empty">{copy("模拟开始后，对白和行动会出现在这里。", "Dialogue and actions will appear here once the simulation starts.")}</div> : null}
              {glimpseRows.map((row) => (
                <article className={`live-transcript-entry ${row.event}`} key={`transcript-${row.seq}-${row.speaker}-${row.event}`}>
                  <header>
                    <strong>{row.speaker}</strong>
                    <time>{row.time}</time>
                  </header>
                  <div>{row.text}</div>
                </article>
              ))}
              {finalResultText ? (
                <>
                  <div className="live-transcript-section-title">{copy("最终演出", "Final Scene")}</div>
                  <article className="live-transcript-entry final-scene">
                    <header><strong>{copy("旁白", "Narrator")}</strong></header>
                    <div>{finalResultText}</div>
                  </article>
                </>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {showRunResults ? (
        <div className="modal-backdrop result-backdrop" role="dialog" aria-modal="true" onClick={() => setShowRunResults(false)}>
          <div className="modal-card run-result-card" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <div>
                <span className="result-kicker">SIMULATION COMPLETE</span>
                <h2>{copy("模拟结束", "Simulation Complete")}</h2>
              </div>
              <button className="btn" onClick={() => setShowRunResults(false)}>{copy("关闭", "Close")}</button>
            </div>
            <div className="run-result-output">
              <strong>{copy("本次结果", "Result")}</strong>
              <div>{finalResultText || copy("运行已经完成，暂无可展示的最终文本。", "The run completed without a final text output.")}</div>
            </div>
            <div className="result-actions">
              <button className="btn" onClick={() => { setShowRunResults(false); setShowLiveTranscript(true); }}>
                {copy("查看完整过程", "View Full Scene")}
              </button>
              <button
                className="btn primary"
                disabled={!runId || reportMutation.isPending}
                onClick={() => runId && reportMutation.mutate({ run: runId, mode: "briefing" })}
              >
                {reportMutation.isPending ? copy("生成中...", "Generating...") : copy("生成正式汇报", "Generate Briefing")}
              </button>
              <button
                className="btn warning"
                disabled={!runId || reportMutation.isPending}
                onClick={() => runId && reportMutation.mutate({ run: runId, mode: "narrative" })}
              >
                {copy("改写为文学作品", "Create Narrative")}
              </button>
            </div>
            {reportResult ? (
              <div className="run-result-report">
                <div className="sidebar-header"><strong>{reportResult.title}</strong><span className="count-chip">{reportResult.mode}</span></div>
                <div>{reportResult.report_markdown}</div>
              </div>
            ) : null}
            {runId ? <MediaStudio runId={runId} sourceText={reportResult?.report_markdown ?? finalResultText} copy={copy} /> : null}
          </div>
        </div>
      ) : null}

      <StudioStatusRail
        copy={copy}
        modelReady={Boolean(modelConfig && (!modelConfig.requires_api_key || modelConfig.has_api_key))}
        modelName={modelConfig?.default_model ?? "gpt-4o-mini"}
        mode={uiMode}
        entityCount={entityCount}
        backgroundCount={backgroundCount}
        runStatus={runStatus}
        eventCount={eventCount}
        onModel={() => setShowModelConfig(true)}
        onMode={() => setActiveStudioPanel("advanced")}
        onBackground={() => {
          setSelectedNodeId(null);
          setSelectedEdgeId(null);
          setActiveStudioPanel("inspect");
        }}
      />

      <div className="layout studio-layout">
        <aside className={`sidebar left studio-drawer studio-drawer-left ${activeStudioPanel === "create" ? "open" : ""}`}>
          <button className="drawer-close" onClick={() => setActiveStudioPanel(null)} aria-label="close-create-panel">
            ×
          </button>
          <div className="sidebar-header">
            <h3>{copy("创建模拟", "Create Simulation")}</h3>
            <span className="count-chip">{createStep}</span>
          </div>
          <div className="create-stepper">
            {[
              ["auto", copy("交给导演", "Director")],
              ["manual", copy("手动搭建", "Manual")],
              ["templates", copy("模板", "Templates")]
            ].map(([key, label]) => (
              <button key={key} className={`step-tab ${createStep === key ? "active" : ""}`} onClick={() => setCreateStep(key as typeof createStep)}>
                {label}
              </button>
            ))}
          </div>
          <div className={`create-section ${createStep === "manual" ? "active" : ""}`}>
          <div className="sidebar-header create-subhead">
            <h4>{copy("添加实体", "Add Entities")}</h4>
            <span className="count-chip">{entityCount}</span>
          </div>
          <EntityPalette lang={lang} counts={entityCounts} onAdd={addEntityNode} />
          {uiMode === "custom" ? (
            <details className="technical-node-details">
              <summary>{copy("开发者底层节点", "Developer Nodes")}</summary>
              {(Object.keys(nodeSpecs).length > 0
                ? (Object.keys(nodeSpecs) as NodeType[])
                : (["director", "agent", "external_agent", "human_checkpoint", "prompt", "tool", "condition"] as NodeType[])
              ).map((type) => (
                <button className="node-card compact-node-card" key={type} onClick={() => addNode(type, nodeSpecs[type])}>
                  <span className="node-card-title">+ {nodeSpecs[type]?.title ?? type}</span>
                </button>
              ))}
            </details>
          ) : null}
          </div>
          <div className="run-box">
            <div className="sidebar-header">
              <h4>{tr("runObserver")}</h4>
              <span className={`status-chip ${statusClass(runStatus)}`}>{runStatus}</span>
            </div>
            <label>
              {copy("本轮目标", "Run Input")}
              <textarea
                value={runInput}
                onChange={(e) => setRunInput(e.target.value)}
                rows={3}
                placeholder={copy("留空则使用场景/工作流名称", "Leave blank to use scenario/workflow name")}
              />
            </label>
            <p>{tr("runId")}: {runId ?? "-"}</p>
            <p>{tr("events")}: {eventCount}</p>
            {runStatus === "waiting_human" ? <p className="wait-hint">{tr("waitingHuman")}</p> : null}
          </div>
          <div className={`template-box create-section ${createStep === "manual" ? "active" : ""}`}>
            <div className="sidebar-header">
              <h4>{tr("roleCards")}</h4>
              <span className="count-chip">{roleCards.length}</span>
            </div>
            <label>
              {tr("name")}
              <input value={newRoleName} onChange={(e) => setNewRoleName(e.target.value)} placeholder={tr("name")} />
            </label>
            <label>
              {tr("archetype")}
              <input value={newRoleArchetype} onChange={(e) => setNewRoleArchetype(e.target.value)} placeholder={tr("archetype")} />
            </label>
            <label>
              {tr("goal")}
              <textarea value={newRoleGoal} onChange={(e) => setNewRoleGoal(e.target.value)} rows={2} placeholder={tr("goal")} />
            </label>
            <label>
              {tr("style")}
              <input value={newRoleStyle} onChange={(e) => setNewRoleStyle(e.target.value)} placeholder={tr("style")} />
            </label>
            <label>
              {tr("boundaries")}
              <textarea value={newRoleBoundaries} onChange={(e) => setNewRoleBoundaries(e.target.value)} rows={2} placeholder={tr("boundaries")} />
            </label>
            <button
              className="btn"
              disabled={newRoleName.trim().length === 0}
              onClick={() => {
                const id = `role_${Date.now().toString(36)}`;
                upsertRoleCard({
                  id,
                  name: newRoleName.trim(),
                  archetype: newRoleArchetype.trim(),
                  goal: newRoleGoal.trim(),
                  style: newRoleStyle.trim(),
                  boundaries: newRoleBoundaries.trim()
                });
                setNewRoleName("");
                setNewRoleArchetype("");
                setNewRoleGoal("");
                setNewRoleStyle("");
                setNewRoleBoundaries("");
              }}
            >
              {tr("addRoleCard")}
            </button>
            <div className="role-card-list">
              {roleCards.length === 0 ? <div className="empty-hint">{tr("noRoleCards")}</div> : null}
              {roleCards.map((card) => (
                <div key={card.id} className="role-card-item">
                  <div className="role-card-head">
                    <strong>{card.name}</strong>
                    <button className="btn" onClick={() => removeRoleCard(card.id)}>
                      {tr("delete")}
                    </button>
                  </div>
                  <div className="role-card-meta">{card.archetype || "custom archetype"}</div>
                  <div className="role-card-text">{card.goal}</div>
                </div>
              ))}
            </div>
          </div>

          {uiMode === "roleplay" ? (
            <div className={`template-box create-section ${createStep === "manual" ? "active" : ""}`}>
              <div className="sidebar-header">
                <h4>{tr("roleplayBoundaries")}</h4>
                <span className="count-chip">{tr("negativePrompts")}</span>
              </div>
              <label>
                {tr("negativePrompts")}
                <textarea
                  value={roleplayNegativePrompts}
                  onChange={(e) => setRoleplayNegativePrompts(e.target.value)}
                  rows={4}
                  placeholder={copy("负面提示词", "Negative prompts")}
                />
              </label>
              <button
                className="btn"
                onClick={() =>
                  updateSimulationMeta({
                    roleplay_boundaries: roleplayNegativePrompts
                      .split("\n")
                      .map((s) => s.trim())
                      .filter((s) => s.length > 0)
                  })
                }
              >
                {tr("saveBoundaries")}
              </button>
            </div>
          ) : null}

          <div className={`template-box create-section ${createStep === "auto" ? "active" : ""}`}>
            <div className="sidebar-header">
              <h4>{tr("autoParse")}</h4>
              <span className="count-chip">{tr("optional")}</span>
            </div>
            <label>
              {tr("planText")}
              <textarea
                value={planText}
                onChange={(e) => setPlanText(e.target.value)}
                rows={5}
                placeholder={copy("输入模拟需求", "Describe the simulation")}
              />
            </label>
            <label>
              {tr("uploadPlan")}
              <input
                type="file"
                accept=".txt,.md,text/plain,text/markdown"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) {
                    return;
                  }
                  const text = await file.text();
                  setPlanText(text);
                }}
              />
            </label>
            <button className="btn" disabled={compilePlanMutation.isPending || planText.trim().length === 0} onClick={() => compilePlanMutation.mutate()}>
              {compilePlanMutation.isPending ? tr("compiling") : tr("generateWorkflow")}
            </button>
            {planDraft ? (
              <div className="director-parse-card confirm-card">
                <div className="semantic-strip">
                  <span>{copy("导演方案", "Director Plan")}</span>
                  <strong>{String(planDraft.extracted?.goal ?? planDraft.workflow.environment.scenario ?? "-")}</strong>
                </div>
                <div className="draft-confirm-section">
                  <strong>{copy("模拟模式", "Simulation Mode")}</strong>
                  <div className="mode-chip-row">
                    {(["roleplay", "research", "custom"] as const).map((mode) => (
                      <button key={mode} className={`btn ${planMode === mode ? "primary" : ""}`} onClick={() => {
                        setPlanMode(mode);
                        if (mode === "research") {
                          setEvidenceReviewed(false);
                          setShowEvidenceModal(true);
                        }
                      }}>
                        {mode === "roleplay" ? copy("角色扮演", "Roleplay") : mode === "research" ? copy("研究", "Research") : copy("自定义", "Custom")}
                      </button>
                    ))}
                  </div>
                  {planMode === "research" ? (
                    <div className="mode-chip-row compact">
                      {(["simulation", "research", "consulting"] as const).map((submode) => (
                        <button key={submode} className={`btn ${planSubmode === submode ? "primary" : ""}`} onClick={() => setPlanSubmode(submode)}>
                          {submode === "simulation" ? copy("现实推演", "Simulation") : submode === "research" ? copy("模拟调研", "Study") : copy("咨询", "Consulting")}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
                <div className="draft-contract">
                  <span>
                    {copy("参与者", "Participants")}: {planDraft.workflow.nodes.filter((node) => node.type === "agent" && !node.id.startsWith("summary_")).length}
                  </span>
                  <span>
                    {copy("交付", "Output")}: {String((planDraft.simulation_blueprint?.output_contract as Record<string, unknown> | undefined)?.format ?? "simulation")}
                  </span>
                  {planMode === "research" ? (
                    <button className="btn" onClick={() => setShowEvidenceModal(true)}>
                      {evidencePack ? copy(`证据 ${evidencePack.items.length} 项`, `${evidencePack.items.length} evidence items`) : copy("添加证据", "Add Evidence")}
                    </button>
                  ) : null}
                  {Number((planDraft.simulation_blueprint?.output_contract as Record<string, unknown> | undefined)?.max_items ?? 0) > 0 ? (
                    <span>
                      {copy("上限", "Limit")}: {String((planDraft.simulation_blueprint?.output_contract as Record<string, unknown>).max_items)} {String((planDraft.simulation_blueprint?.output_contract as Record<string, unknown>).unit)}
                    </span>
                  ) : null}
                </div>
                <div className="draft-preview">
                  <div className="draft-summary">
                    <strong>{copy("背景", "Background")}</strong>
                    <p>{planDraft.workflow.environment.scenario || planDraft.workflow.environment.profile || "-"}</p>
                  </div>
                  <div className="draft-node-list">
                    {planDraft.workflow.nodes.filter((node) => node.type === "agent" && !node.id.startsWith("summary_")).map((node) => {
                      const config = node.config ?? {};
                      const name = String(config.entity_name ?? config.profile ?? node.id);
                      const entityType = String(config.entity_type ?? node.type);
                      const responsibilities = Array.isArray(config.responsibilities) ? config.responsibilities.join(", ") : "";
                      const profile = String(config.entity_profile ?? config.objective ?? config.template ?? "");
                      return (
                        <div key={node.id} className={`draft-node-row draft-node-${entityType}`}>
                          <span>{node.id}</span>
                          <strong>{name}</strong>
                          <small>{entityType}</small>
                          {responsibilities ? <p>{responsibilities}</p> : profile ? <p>{profile}</p> : null}
                        </div>
                      );
                    })}
                  </div>
                </div>
                <div className="mode-chip-row">
                  <button className="btn primary" disabled={isApplyingPlanDraft} onClick={() => void applyPlanDraft()}>
                    {isApplyingPlanDraft
                      ? copy("正在处理", "Working")
                      : draftNeedsRegeneration
                        ? copy("按此模式更新方案", "Update For This Mode")
                        : copy("确认并应用", "Confirm & Apply")}
                  </button>
                  <button className="btn" onClick={() => setPlanDraft(null)}>
                    {copy("放弃草案", "Discard Draft")}
                  </button>
                </div>
              </div>
            ) : null}
          </div>

          <div className={`template-box create-section ${createStep === "manual" ? "active" : ""}`}>
            <div className="sidebar-header">
              <h4>{tr("manualSetup")}</h4>
              <span className="count-chip">{tr("manual")}</span>
            </div>
            <div className="topbar-actions">
              <button className="btn" onClick={() => addAgentPreset("planner")}>+ Planner</button>
              <button className="btn" onClick={() => addAgentPreset("coder")}>+ Coder</button>
            </div>
            <div className="topbar-actions">
              <button className="btn" onClick={() => addAgentPreset("reviewer")}>+ Reviewer</button>
              <button className="btn" onClick={() => addNode("human_checkpoint", nodeSpecs.human_checkpoint)}>+ Human Checkpoint</button>
            </div>
          </div>

          <div className={`template-box protocol-box create-section ${createStep === "templates" ? "active" : ""}`}>
            <div className="sidebar-header">
              <h4>{copy("外接 Agent 执行方式", "External Agent Execution")}</h4>
              <span className="count-chip">Text v1</span>
            </div>
            <div className="protocol-mini">
              <span>POST /agent/tasks</span>
              <span>input.text + context.environment + context.interactions</span>
              <span>output.text</span>
            </div>
            <div className="mode-chip-row">
              <button
                className="btn"
                onClick={() =>
                  navigator.clipboard
                    ?.writeText("http://localhost:8000/api/protocols/text-agent-v1")
                    .catch(() => undefined)
                }
              >
                {copy("复制协议地址", "Copy Protocol URL")}
              </button>
            </div>
          </div>

          <div className={`template-box create-section ${createStep === "templates" ? "active" : ""}`}>
            <div className="sidebar-header">
              <h4>{tr("templates")}</h4>
              <span className="count-chip">{templates.length}</span>
            </div>
            <label>
              {tr("name")}
              <input value={templateName} onChange={(e) => setTemplateName(e.target.value)} placeholder="Template name" />
            </label>
            <label>
              {tr("category")}
              <input value={templateCategory} onChange={(e) => setTemplateCategory(e.target.value)} placeholder="general / research / roleplay" />
            </label>
            <label>
              {tr("tags")}
              <input value={templateTags} onChange={(e) => setTemplateTags(e.target.value)} placeholder="comma,separated,tags" />
            </label>
            <label>
              {tr("description")}
              <textarea value={templateDescription} onChange={(e) => setTemplateDescription(e.target.value)} rows={2} />
            </label>
            <button className="btn" onClick={() => saveTemplateMutation.mutate()} disabled={saveTemplateMutation.isPending}>
              {saveTemplateMutation.isPending ? tr("saving") : tr("saveTemplate")}
            </button>
            <div className="template-list">
              {templates.length === 0 ? <div className="empty-hint">{tr("noTemplates")}</div> : null}
              {templates.map((tpl) => (
                <button key={tpl.id} className="template-item" onClick={() => applyTemplate(tpl.id)}>
                  <span className="template-name">{tpl.name}</span>
                  <span className="template-meta">
                    {tpl.category} {tpl.tags.length > 0 ? `| ${tpl.tags.join(", ")}` : ""}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </aside>

        <main ref={canvasRef} className="canvas studio-canvas">
          <StudioDock
            isStarting={runMutation.isPending}
            isRunning={["queued", "pending", "running"].includes(runStatus)}
            isStopping={runStatus === "stopping" || stopMutation.isPending}
            tr={tr}
            onCreate={() => setActiveStudioPanel("create")}
            onInspect={() => setActiveStudioPanel("inspect")}
            onAdvanced={() => setActiveStudioPanel("advanced")}
            onRun={() => runMutation.mutate()}
            onStop={() => stopMutation.mutate()}
          />
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_, node) => setSelectedNodeId(node.id)}
            onNodeMouseEnter={(evt, node) => {
              if (!hoverEnabled) {
                return;
              }
              const card = findRoleCardForNode(node, roleCards);
              if (!card) {
                setHoverRoleCard(null);
                return;
              }
              setHoverRoleCard({
                nodeId: node.id,
                x: evt.clientX + 12,
                y: evt.clientY + 12,
                card
              });
            }}
            onNodeMouseMove={(evt, node) => {
              if (!hoverEnabled || !hoverRoleCard || hoverRoleCard.nodeId !== node.id) {
                return;
              }
              setHoverRoleCard({
                ...hoverRoleCard,
                x: evt.clientX + 12,
                y: evt.clientY + 12
              });
            }}
            onNodeMouseLeave={() => setHoverRoleCard(null)}
            onEdgeClick={(_, edge) => setSelectedEdgeId(edge.id)}
            onPaneClick={() => {
              setSelectedNodeId(null);
              setSelectedEdgeId(null);
              setHoverRoleCard(null);
            }}
            zoomOnScroll={false}
            panOnScroll={false}
            preventScrolling={false}
            fitView
          >
            <MiniMap />
            <Controls />
            <Background gap={24} size={1.2} />
          </ReactFlow>
          <section
            className="canvas-live-window"
            role="button"
            tabIndex={0}
            onClick={() => setShowLiveTranscript(true)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                setShowLiveTranscript(true);
              }
            }}
          >
            <div className="live-window-head">
              <span>{tr("liveBroadcast")}</span>
              <strong>{runStatus}</strong>
            </div>
            {runStatus === "succeeded" ? (
              <div className="live-window-complete">
                <strong>{copy("模拟结束", "Simulation Complete")}</strong>
                <span>{copy("点击查看结果", "Open results")}</span>
              </div>
            ) : currentGlimpse ? (
              <div className="live-window-main">
                <small>
                  {currentGlimpse.speaker} · {currentGlimpse.time}
                </small>
                <p>{toBroadcastLine(currentGlimpse, uiMode, broadcastTone)}</p>
              </div>
            ) : (
              <div className="live-window-empty">{copy("暂无播报", "No broadcast")}</div>
            )}
          </section>
          <div className="director-orb-wrap" style={{ left: directorPosition.x, top: directorPosition.y }}>
            <button
              type="button"
              className="director-orb"
              onPointerDown={onDirectorPointerDown}
              onPointerMove={onDirectorPointerMove}
              onPointerUp={onDirectorPointerUp}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setShowDirectorBubble((value) => !value);
                }
              }}
              aria-label="director"
            >
              <span className="cat-ear cat-ear-left" />
              <span className="cat-ear cat-ear-right" />
              <span className="cat-face" />
              <span className="cat-eye cat-eye-left" />
              <span className="cat-eye cat-eye-right" />
              <span className="cat-muzzle" />
              <span className="cat-nose" />
              <span className="cat-scarf" />
              <span className="cat-tail" />
              <span className="cat-label">Director</span>
            </button>
            {showDirectorBubble ? (
              <div className="director-bubble">
                <div className="director-bubble-head">
                  <strong>{tr("directorConsole")}</strong>
                  <button className="btn" onClick={() => setShowDirectorBubble(false)}>
                    ×
                  </button>
                </div>
                <textarea
                  rows={4}
                  value={directorText}
                  onChange={(e) => setDirectorText(e.target.value)}
                  placeholder={runId ? copy("输入导演指令", "Director command") : copy("告诉导演你想模拟什么", "Tell the Director what to arrange")}
                />
                {runId ? (
                  <div className="mode-chip-row">
                    <select value={directorScope} onChange={(e) => setDirectorScope(e.target.value as "global" | "phase" | "node")}>
                      <option value="global">global</option>
                      <option value="phase">phase</option>
                      <option value="node">node</option>
                    </select>
                    <button
                      className="btn warning"
                      disabled={directorText.trim().length === 0 || directorCommandMutation.isPending}
                      onClick={() => directorCommandMutation.mutate()}
                    >
                      {directorCommandMutation.isPending ? tr("applying") : tr("sendToDirector")}
                    </button>
                  </div>
                ) : (
                  <div className="mode-chip-row">
                    <button
                      className="btn warning"
                      disabled={directorText.trim().length === 0 || directorArrangeMutation.isPending}
                      onClick={() => directorArrangeMutation.mutate()}
                    >
                      {directorArrangeMutation.isPending ? copy("安排中...", "Arranging...") : copy("让导演安排", "Let Director Arrange")}
                    </button>
                    <button
                      className="btn"
                      onClick={() => {
                        setPlanText(directorText);
                        setActiveStudioPanel("create");
                        setCreateStep("auto");
                      }}
                    >
                      {copy("打开创建", "Open Create")}
                    </button>
                  </div>
                )}
                {directorResult ? <div className="empty-hint">{directorResult.guidance}</div> : null}
              </div>
            ) : null}
          </div>
          {hoverEnabled && hoverRoleCard ? (
            <div className="hover-role-card" style={{ left: hoverRoleCard.x, top: hoverRoleCard.y }}>
              <div className="hover-role-title">{hoverRoleCard.card.name}</div>
              <div className="hover-role-meta">{hoverRoleCard.card.archetype || "custom role"}</div>
              {hoverRoleCard.card.goal ? <div className="hover-role-line">Goal: {hoverRoleCard.card.goal}</div> : null}
              {hoverRoleCard.card.style ? <div className="hover-role-line">Style: {hoverRoleCard.card.style}</div> : null}
              {hoverRoleCard.card.boundaries ? <div className="hover-role-line">Boundary: {hoverRoleCard.card.boundaries}</div> : null}
            </div>
          ) : null}
        </main>

        <aside
          className={`sidebar right studio-drawer studio-drawer-right ${activeStudioPanel === "inspect" || activeStudioPanel === "advanced" ? "open" : ""} ${
            activeStudioPanel === "advanced" ? "advanced-open" : "inspect-open"
          }`}
        >
          <button className="drawer-close" onClick={() => setActiveStudioPanel(null)} aria-label="close-inspector-panel">
            ×
          </button>
          <div className="sidebar-header">
            <h3>{activeStudioPanel === "advanced" ? tr("advanced") : tr("inspector")}</h3>
            <span className="selection-chip">
              {activeStudioPanel === "advanced"
                ? `${runStatus} · ${eventCount}`
                : selectedNode
                  ? `${tr("node")}: ${selectedNode.id}`
                  : selectedEdge
                    ? `Edge: ${selectedEdge.id}`
                    : tr("global")}
            </span>
          </div>
          <div className="inspector-panel">
          {selectedNode ? (
            <>
              <label className="advanced-toggle">
                <input type="checkbox" checked={showAdvanced} onChange={(e) => setShowAdvanced(e.target.checked)} />
                {tr("showAdvanced")}
              </label>
              <NodeConfigPanel
                node={selectedNode}
                spec={selectedSpec}
                showAdvanced={showAdvanced}
                onConfigChange={(k, v) => updateNodeConfig(selectedNode.id, k, v)}
                onInputChange={(k, v) => updateNodeInput(selectedNode.id, k, v)}
              />
            </>
          ) : selectedEdge ? (
            <EdgeInteractionPanel
              edge={selectedEdge}
              modes={interactionModes}
              onChange={(k, v) => updateEdgeInteraction(selectedEdge.id, k, v)}
            />
          ) : (
            <EnvironmentPanel
              environment={workflow.environment}
              nodes={nodes}
              onChange={(k, v) => setEnvironmentField(k, v)}
            />
          )}
          </div>

          <div className="advanced-panel">
          <div className="sidebar-header logs-header">
            <h3>{copy("高级控制台", "Advanced Console")}</h3>
            <span className="count-chip">{eventCount}</span>
          </div>

          <div className="analytics-block product-settings-block">
            <div className="sidebar-header logs-header">
              <h3>{copy("产品设置", "Product Settings")}</h3>
              <span className="count-chip">AUV</span>
            </div>
            <div className="mode-chip-row">
              {(["research", "roleplay", "custom"] as UiMode[]).map((mode) => (
                <button key={mode} className={`btn ${uiMode === mode ? "primary" : ""}`} onClick={() => onUiModeChange(mode)}>
                  {mode}
                </button>
              ))}
            </div>
            <label>
              {copy("展开粒度", "Detail Granularity")}
              <select value={detailGranularity} onChange={(e) => updateSimulationMeta({ detail_granularity: e.target.value })}>
                <option value="concise">{copy("简洁：推进结果优先", "Concise: outcome first")}</option>
                <option value="detailed">{copy("细粒度：展开具体过程", "Detailed: expand concrete process")}</option>
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={preflightConfig.enabled}
                onChange={(e) =>
                  updateSimulationMeta({
                    preflight_confirm: {
                      ...(simulationMeta.preflight_confirm as Record<string, unknown> | undefined),
                      enabled: e.target.checked
                    }
                  })
                }
              />
              {tr("preflight")}
            </label>
            <div className="settings-grid-compact">
              <label>
                {tr("warmupNodes")}
                <input
                  type="number"
                  min={0}
                  max={20}
                  value={preflightConfig.warmupNodes}
                  onChange={(e) =>
                    updateSimulationMeta({
                      preflight_confirm: {
                        ...(simulationMeta.preflight_confirm as Record<string, unknown> | undefined),
                        warmup_nodes: Number(e.target.value || "1")
                      }
                    })
                  }
                />
              </label>
              <label>
                {tr("minExpensive")}
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={preflightConfig.minRemainingExpensiveNodes}
                  onChange={(e) =>
                    updateSimulationMeta({
                      preflight_confirm: {
                        ...(simulationMeta.preflight_confirm as Record<string, unknown> | undefined),
                        min_remaining_expensive_nodes: Number(e.target.value || "1")
                      }
                    })
                  }
                />
              </label>
            </div>
            <label>
              {tr("confirmQuestion")}
              <textarea
                rows={2}
                value={preflightConfig.question}
                onChange={(e) =>
                  updateSimulationMeta({
                    preflight_confirm: {
                      ...(simulationMeta.preflight_confirm as Record<string, unknown> | undefined),
                      question: e.target.value
                    }
                  })
                }
              />
            </label>
            <div className="seed-strip">
              <span>{tr("seed")}</span>
              <strong>{simulationSeed || tr("notSet")}</strong>
            </div>
            <div className="mode-chip-row">
              <button
                className="btn"
                onClick={() => {
                  const seed = simulationSeed || `seed_${Date.now().toString(36)}`;
                  navigator.clipboard?.writeText(seed).catch(() => undefined);
                }}
              >
                {tr("copySeed")}
              </button>
              <button className="btn" onClick={() => updateSimulationMeta({ seed: `seed_${Date.now().toString(36)}` })}>
                {tr("regenerate")}
              </button>
            </div>
          </div>

          <div className="analytics-block developer-settings-block">
            <div className="sidebar-header logs-header">
              <h3>{copy("开发者设置", "Developer Settings")}</h3>
              <span className="count-chip">protocol</span>
            </div>
            <details className="technical-node-details advanced-technical-details">
              <summary>{copy("底层节点", "Developer Nodes")}</summary>
              {(Object.keys(nodeSpecs).length > 0
                ? (Object.keys(nodeSpecs) as NodeType[])
                : (["director", "agent", "external_agent", "human_checkpoint", "prompt", "tool", "condition"] as NodeType[])
              ).map((type) => (
                <button className="node-card compact-node-card" key={type} onClick={() => addNode(type, nodeSpecs[type])}>
                  <span className="node-card-title">+ {nodeSpecs[type]?.title ?? type}</span>
                </button>
              ))}
            </details>
            <div className="protocol-mini">
              <span>POST /agent/tasks</span>
              <span>input.text + context.environment + context.interactions</span>
              <span>output.text</span>
            </div>
            <button
              className="btn"
              onClick={() =>
                navigator.clipboard
                  ?.writeText("http://localhost:8000/api/protocols/text-agent-v1")
                  .catch(() => undefined)
              }
            >
              {copy("复制协议地址", "Copy Protocol URL")}
            </button>
          </div>
          {modeVisibility.director ? (
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("directorConsole")}</h3>
              <span className="count-chip">{directorCapabilities.length} caps</span>
            </div>
            <label>
              {tr("naturalCommand")}
              <textarea
                rows={3}
                value={directorText}
                onChange={(e) => setDirectorText(e.target.value)}
                placeholder={copy("输入导演指令", "Director command")}
              />
            </label>
            <label>
              {tr("scope")}
              <select value={directorScope} onChange={(e) => setDirectorScope(e.target.value as "global" | "phase" | "node")}>
                <option value="global">global</option>
                <option value="phase">phase</option>
                <option value="node">node</option>
              </select>
            </label>
            {directorScope === "node" ? (
              <label>
                {tr("targetNode")}
                <input value={directorTargetNode} onChange={(e) => setDirectorTargetNode(e.target.value)} placeholder="node_id" />
              </label>
            ) : null}
            <button
              className="btn warning"
              disabled={!runId || directorText.trim().length === 0 || directorCommandMutation.isPending}
              onClick={() => directorCommandMutation.mutate()}
            >
              {directorCommandMutation.isPending ? tr("applying") : tr("sendToDirector")}
            </button>
            {directorResult ? (
              <div className="monitor-feed">
                <div className="monitor-item warn">
                  <div className="monitor-top">
                    <span className="monitor-seq">#{directorResult.event_seq ?? "-"}</span>
                    <span className="monitor-node">{directorResult.applied_capability}</span>
                    <span className="monitor-level warn">{directorResult.accepted ? "accepted" : "rejected"}</span>
                  </div>
                  <div className="monitor-msg">{directorResult.guidance}</div>
                </div>
              </div>
            ) : (
              <div className="empty-hint">{tr("noDirectorCommand")}</div>
            )}
          </div>
          ) : null}

          {modeVisibility.glimpse ? (
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("liveBroadcast")}</h3>
              <span className="count-chip">{glimpseSnapshot.length}</span>
            </div>
            {currentGlimpse ? (
              <div className="glimpse-hero">
                <div className="glimpse-meta">
                  <span>#{currentGlimpse.seq}</span>
                  <span>{currentGlimpse.speaker}</span>
                  <span>{currentGlimpse.event}</span>
                  <span>{currentGlimpse.time}</span>
                </div>
                <div className="glimpse-text">{toBroadcastLine(currentGlimpse, uiMode, broadcastTone)}</div>
              </div>
            ) : (
              <div className="empty-hint">{tr("noBroadcast")}</div>
            )}
            <div className="glimpse-feed">
              {broadcastRows.map((row) => (
                <div key={`${row.seq}-${row.speaker}-${row.event}`} className="glimpse-item">
                  <div className="glimpse-meta">
                    <span>#{row.seq}</span>
                    <span>{row.speaker}</span>
                    <span>{row.event}</span>
                    <span>{row.time}</span>
                  </div>
                  <div className="glimpse-text">{row.line}</div>
                </div>
              ))}
            </div>
          </div>
          ) : null}

          {modeVisibility.human ? (
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("humanIntervention")}</h3>
              <span className={`count-chip ${runStatus === "waiting_human" ? "warn" : ""}`}>
                {runStatus === "waiting_human" ? tr("required") : tr("idle")}
              </span>
            </div>
            {runStatus === "waiting_human" && waitingEvent ? (
              <>
                <div className="metric-inline">
                  <span>{tr("node")}:</span>
                  <span>{waitingNodeId || "-"}</span>
                </div>
                <label>
                  {tr("checkpointQuestion")}
                  <textarea
                    readOnly
                    rows={4}
                    value={String((waitingEvent.payload.output as Record<string, unknown> | undefined)?.question ?? "Human input required")}
                  />
                </label>
                {(() => {
                  const preview = (waitingEvent.payload.output as Record<string, unknown> | undefined)?.preview;
                  if (!preview) {
                    return null;
                  }
                  return (
                    <label>
                      {tr("directorPreview")}
                      <textarea readOnly rows={6} value={JSON.stringify(preview, null, 2)} />
                    </label>
                  );
                })()}
                <div className="decision-grid">
                  <button className={`btn ${decisionAction === "continue" ? "primary" : ""}`} onClick={() => setDecisionAction("continue")}>
                    {tr("continue")}
                  </button>
                  <button className={`btn ${decisionAction === "adjust_direction" ? "primary" : ""}`} onClick={() => setDecisionAction("adjust_direction")}>
                    {tr("adjustDirection")}
                  </button>
                  <button className={`btn ${decisionAction === "reduce_cost" ? "primary" : ""}`} onClick={() => setDecisionAction("reduce_cost")}>
                    {tr("reduceCost")}
                  </button>
                  <button className={`btn ${decisionAction === "inject_event" ? "primary" : ""}`} onClick={() => setDecisionAction("inject_event")}>
                    {tr("injectEvent")}
                  </button>
                </div>
                <label>
                  {tr("responder")}
                  <input value={humanResponder} onChange={(e) => setHumanResponder(e.target.value)} placeholder={tr("responder")} />
                </label>
                <label>
                  {tr("responseOptional")}
                  <textarea
                    rows={4}
                    value={humanResponseText}
                    onChange={(e) => setHumanResponseText(e.target.value)}
                    placeholder={tr("responseOptional")}
                  />
                </label>
                <button
                  className="btn warning"
                  disabled={!runId || !waitingNodeId || humanResponseMutation.isPending}
                  onClick={() => {
                    if (!runId || !waitingNodeId) {
                      return;
                    }
                    const checkpoint = (waitingEvent.payload.output as Record<string, unknown> | undefined) ?? {};
                    const contextHint = String(checkpoint.context_hint ?? "");
                    const preset = decisionActionToText(decisionAction);
                    const mergedResponse = humanResponseText.trim() ? `${preset}\n${humanResponseText.trim()}` : preset;
                    humanResponseMutation.mutate({
                      node_id: waitingNodeId,
                      response: mergedResponse,
                      responder: humanResponder.trim() || "user",
                      metadata: {
                        source: "frontend_panel",
                        kind: contextHint === "director_preflight_confirm" ? "director_preflight_confirm" : "human_checkpoint",
                        action: decisionAction
                      }
                    });
                  }}
                >
                  {humanResponseMutation.isPending ? tr("submitting") : tr("resumeRun")}
                </button>
              </>
            ) : (
              <div className="empty-hint">{tr("noHumanCheckpoint")}</div>
            )}
          </div>
          ) : null}

          {modeVisibility.monitor ? (
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("lightMemory")}</h3>
              <span className="count-chip">{memories.length}</span>
            </div>
            <div className="metric-inline">
              <span>Workflow:</span>
              <span>{workflow.id}</span>
            </div>
            <button className="btn" disabled={clearMemoryMutation.isPending || memories.length === 0} onClick={() => clearMemoryMutation.mutate()}>
              {clearMemoryMutation.isPending ? tr("clearing") : tr("clearMemory")}
            </button>
            <div className="monitor-feed">
              {memories.length === 0 ? <div className="empty-hint">{tr("noMemory")}</div> : null}
              {memories.map((m) => (
                <div key={m.id} className={`monitor-item ${m.kind === "state" ? "warn" : m.kind === "character" ? "info" : "success"}`}>
                  <div className="monitor-top">
                    <span className="monitor-seq">#{m.id}</span>
                    <span className="monitor-node">{m.subject || m.role || m.node_id}</span>
                    <span className={`monitor-level ${m.kind === "state" ? "warn" : m.kind === "fact" ? "success" : "info"}`}>{m.kind}</span>
                  </div>
                  <div className="monitor-msg">{m.content}</div>
                  <small>
                    {new Date(m.created_at).toLocaleTimeString()}
                    {m.source_event_seq ? ` / event#${m.source_event_seq}` : ""}
                    {` / importance ${m.importance.toFixed(2)}`}
                  </small>
                </div>
              ))}
            </div>
          </div>
          ) : null}

          {modeVisibility.simulation ? (
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("liveMonitor")}</h3>
              <span className="count-chip">{monitorRows.length} rows</span>
            </div>
            <div className="metric-inline">
              <span>Running: {monitorStats.running}</span>
              <span>Succeeded: {monitorStats.succeeded}</span>
              <span>Failed: {monitorStats.failed}</span>
              <span>Waiting: {monitorStats.waiting}</span>
            </div>
            <label>
              {tr("granularity")}
              <select value={monitorMode} onChange={(e) => setMonitorMode(e.target.value as "fine" | "balanced" | "coarse")}>
                <option value="fine">fine (task collaboration)</option>
                <option value="balanced">balanced</option>
                <option value="coarse">coarse (research simulation)</option>
              </select>
            </label>
            <label>
              {tr("nodeFilter")}
              <select value={monitorNodeFilter} onChange={(e) => setMonitorNodeFilter(e.target.value)}>
                <option value="all">all</option>
                {monitorNodeOptions.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <div className="monitor-feed">
              {monitorRows.length === 0 ? <div className="empty-hint">{tr("noMonitor")}</div> : null}
              {monitorRows.map((r, idx) => (
                <div key={`${r.seq ?? idx}-${r.nodeId}-${r.level}`} className={`monitor-item ${r.level}`}>
                  <div className="monitor-top">
                    <span className="monitor-seq">#{r.seq ?? "-"}</span>
                    <span className="monitor-node">{r.nodeId}</span>
                    <span className={`monitor-level ${r.level}`}>{r.level}</span>
                  </div>
                  <div className="monitor-msg">{r.message}</div>
                  <small>{r.time}</small>
                </div>
              ))}
            </div>
          </div>
          ) : null}

          {modeVisibility.trace ? (
          <>
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("simulationView")}</h3>
              <span className="count-chip">{simulationView.phaseName || "n/a"}</span>
            </div>
            <div className="metric-inline">
              <span>Step: {simulationView.stepSeq ?? "-"}</span>
              <span>Phase: {simulationView.phaseName || "-"}</span>
            </div>
            <div className="metric-inline world-state-inline">
              <span>{copy("时间", "Time")}: {simulationView.worldState.current_time || simulationView.worldState.temporal_scope || "-"}</span>
              <span>{copy("空间", "Space")}: {simulationView.worldState.current_location || simulationView.worldState.spatial_scope || "-"}</span>
            </div>
            <div className="metrics-grid">
                  <MetricCard label={copy("进度", "Progress")} value={formatRatio(simulationView.variables.progress)} />
                  <MetricCard label={copy("置信度", "Confidence")} value={formatRatio(simulationView.variables.confidence)} />
                  <MetricCard label={copy("风险", "Risk")} value={formatRatio(simulationView.variables.risk)} />
                  <MetricCard label={copy("对齐度", "Alignment")} value={formatRatio(simulationView.variables.alignment)} />
            </div>
            {simulationView.dynamicState.enabled ? (
              <div className="dynamic-state-panel">
                <div className="dynamic-state-head">
                  <strong>{copy("当前状态", "Current State")}</strong>
                  <span>v{simulationView.dynamicState.version} · {simulationView.dynamicState.concepts.length}</span>
                </div>
                {simulationView.dynamicState.concepts.map((concept) => (
                  <div className="dynamic-state-row" key={concept.id} title={concept.description}>
                    <span>{concept.id.replaceAll("_", " ")}</span>
                    <strong>{formatDynamicStateValue(concept.value)}</strong>
                    {concept.confidence != null ? <small>{formatRatio(concept.confidence)}</small> : null}
                  </div>
                ))}
                {simulationView.dynamicState.concepts.length === 0 ? <div className="empty-hint">{copy("等待状态形成", "Awaiting state")}</div> : null}
              </div>
            ) : (
              <div className="metric-inline">
                <span>Task: {simulationView.stateMemory.task_state}</span>
                <span>Collab: {simulationView.stateMemory.collaboration_state}</span>
                <span>Relation: {simulationView.stateMemory.relationship_state}</span>
              </div>
            )}
            <div className="relationship-map">
              {relationshipRows.length === 0 ? <div className="empty-hint">{tr("relationshipMapEmpty")}</div> : null}
              {relationshipRows.slice(0, 10).map((row) => (
                <div key={row.id} className="relationship-link">
                  <span className="relationship-node">{row.source}</span>
                  <span className="relationship-arrow">{row.mode}</span>
                  <span className="relationship-node">{row.target}</span>
                  <span className="relationship-meta">
                    {row.relation} / {row.intensity}
                  </span>
                </div>
              ))}
            </div>
            <div className="monitor-feed">
              {simulationView.timeline.length === 0 ? <div className="empty-hint">{copy("暂无模拟轨迹。", "No simulation trace yet.")}</div> : null}
              {simulationView.timeline.slice(-8).reverse().map((row) => (
                <div key={`${row.seq}-${row.nodeId}`} className="monitor-item info">
                  <div className="monitor-top">
                    <span className="monitor-seq">#{row.seq}</span>
                    <span className="monitor-node">{row.nodeId}</span>
                    <span className="monitor-level info">{row.phaseName || "-"}</span>
                  </div>
                  <div className="monitor-msg">{row.status}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="event-list">
            {events.length === 0 ? <div className="empty-hint">{tr("noRuntimeEvents")}</div> : null}
            {visibleEvents.map((e, idx) => (
              <div key={`${e.seq ?? idx}-${e.node_id}`} className={`event ${e.event}`}>
                <div className="event-top">
                  <span className="event-id">#{e.seq ?? "-"}</span>
                  <span className={`event-badge ${e.event}`}>{e.event}</span>
                  <span className="event-node">{e.node_id}</span>
                  {e.seq ? (
                    <button
                      className="btn"
                      onClick={() => inspectTrace(Number(e.seq))}
                      disabled={!runId}
                      aria-label={`trace-${e.seq}`}
                    >
                      {tr("trace")}
                    </button>
                  ) : null}
                </div>
                <small>{new Date(e.timestamp).toLocaleTimeString()}</small>
              </div>
            ))}
          </div>
          </>
          ) : null}

          {modeVisibility.timeline ? (
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("traceInspector")}</h3>
              <span className={`count-chip ${auditVerify && !auditVerify.verified ? "warn" : ""}`}>
                {auditVerify ? (auditVerify.verified ? "audit ok" : "audit broken") : "-"}
              </span>
            </div>
            <div className="metric-inline">
              <span>Selected Seq: {traceSeq ?? "-"}</span>
              <span>{copy("已检查", "Checked")}: {auditVerify?.checked_events ?? 0}/{auditVerify?.total_events ?? 0}</span>
            </div>
            <button
              className="btn"
              disabled={!runId}
              onClick={() => {
                if (!runId) {
                  return;
                }
                api.verifyRunAudit(runId).then((v) => setAuditVerify(v)).catch(() => setAuditVerify(null));
              }}
            >
              {copy("验证审计链", "Verify Audit Chain")}
            </button>
            <div className="trace-section rollback-section">
              <div className="metric-inline">
                <strong>{copy("回滚检查点", "Rollback Checkpoints")}</strong>
                <button
                  className="btn"
                  disabled={!runId || checkpointsMutation.isPending}
                  onClick={() => {
                    if (runId) {
                      checkpointsMutation.mutate(runId);
                    }
                  }}
                >
                  {checkpointsMutation.isPending ? copy("刷新中...", "Refreshing...") : copy("刷新", "Refresh")}
                </button>
              </div>
              <label>
                {copy("回滚理由", "Rollback Reason")}
                <input
                  value={rollbackReason}
                  onChange={(e) => setRollbackReason(e.target.value)}
                  placeholder={copy("可选：记录为什么从这里重开", "Optional: why restart here")}
                />
              </label>
              <div className="checkpoint-list">
                {runCheckpoints.length === 0 ? <div className="empty-hint">{copy("暂无可回滚检查点", "No rollback checkpoints")}</div> : null}
                {runCheckpoints.slice(0, 12).map((checkpoint) => (
                  <div key={`checkpoint-${checkpoint.seq}`} className={`checkpoint-item ${checkpoint.event}`}>
                    <div className="monitor-top">
                      <span className="monitor-seq">#{checkpoint.seq}</span>
                      <span className="monitor-node">{checkpoint.node_id}</span>
                      <span className={`monitor-level ${checkpoint.event}`}>{checkpoint.event}</span>
                    </div>
                    {checkpoint.summary ? <div className="monitor-msg">{checkpoint.summary}</div> : null}
                    <div className="metric-inline">
                      <small>{new Date(checkpoint.timestamp).toLocaleTimeString()}</small>
                      <button
                        className="btn warning"
                        disabled={!checkpoint.can_rollback || rollbackMutation.isPending}
                        onClick={() => rollbackMutation.mutate(checkpoint)}
                      >
                        {rollbackMutation.isPending ? copy("回滚中...", "Rolling back...") : copy("从这里重开", "Restart Here")}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            {auditVerify && !auditVerify.verified ? (
              <div className="empty-hint">{copy("断裂位置", "Broken at seq")} #{auditVerify.broken_at_seq ?? "-"}: {auditVerify.message}</div>
            ) : null}
            {traceError ? <div className="empty-hint">{traceError}</div> : null}
            {traceContext ? (
              <div className="trace-panel">
                <div className="metric-inline">
                  <span>{copy("原因", "Cause")}: {traceContext.caused_by || "-"}</span>
                  <span>{copy("链", "Chain")}: {traceContext.chain_ok ? "ok" : "broken"}</span>
                </div>
                <div className="trace-section">
                  <strong>{copy("父事件", "Parent Events")}</strong>
                  {traceContext.parent_events.length === 0 ? <div className="empty-hint">{copy("没有父事件。", "No parent events.")}</div> : null}
                  {traceContext.parent_events.map((p) => (
                    <button key={`parent-${p.seq}`} className="trace-parent" onClick={() => p.seq && inspectTrace(Number(p.seq))}>
                      #{p.seq} {p.node_id} {p.event}
                    </button>
                  ))}
                </div>
                <div className="trace-section">
                  <strong>{copy("上下文快照", "Context Snapshot")}</strong>
                  <pre>{JSON.stringify(traceContext.context_snapshot, null, 2)}</pre>
                </div>
              </div>
            ) : (
              <div className="empty-hint">{copy("未选择事件", "No event selected")}</div>
            )}
          </div>
          ) : null}

          {modeVisibility.metrics ? (
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("timeline")}</h3>
              <button className="btn" onClick={() => setIsTimelinePlaying((v) => !v)} disabled={maxSeq <= 0}>
                {isTimelinePlaying ? copy("暂停", "Pause") : copy("播放", "Play")}
              </button>
            </div>
            <input
              type="range"
              min={0}
              max={Math.max(maxSeq, 1)}
              value={Math.min(timelineSeq, Math.max(maxSeq, 1))}
              onChange={(e) => {
                setIsTimelinePlaying(false);
                setTimelineSeq(Number(e.target.value));
              }}
            />
            <div className="metric-inline">
              <span>{copy("当前序号", "Current Seq")}: {timelineSeq}</span>
              <span>{copy("最大序号", "Max Seq")}: {maxSeq}</span>
            </div>
          </div>
          ) : null}

          {modeVisibility.compare ? (
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("runMetrics")}</h3>
              <span className="count-chip">{metrics ? "live" : "n/a"}</span>
            </div>
            {metrics ? (
              <div className="metrics-grid">
                <MetricCard label={copy("耗时", "Duration")} value={formatMs(metrics.duration_ms)} />
                <MetricCard label={copy("Token 估算", "Token Est.")} value={String(metrics.estimated_token_usage)} />
                <MetricCard label={copy("成功", "Succeeded")} value={String(metrics.succeeded_nodes)} />
                <MetricCard label={copy("失败", "Failed")} value={String(metrics.failed_nodes)} />
                <MetricCard label={copy("互动", "Interactions")} value={String(metrics.total_interactions)} />
                <MetricCard label={copy("导演分", "Director Score")} value={metrics.director_avg_score?.toFixed(2) ?? "-"} />
              </div>
            ) : (
              <div className="empty-hint">{copy("暂无指标", "No metrics")}</div>
            )}
            {metrics ? (
              <div className="metric-inline">
                <span>{copy("导演变化", "Director Delta")}: {metrics.director_effect.delta_score?.toFixed(2) ?? "-"}</span>
                <span>{metrics.director_effect.improved ? copy("改善", "Improved") : copy("未改善", "Not improved")}</span>
              </div>
            ) : null}
          </div>
          ) : null}

          {modeVisibility.report ? (
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("abCompare")}</h3>
              <span className="count-chip">{compareResult ? compareResult.winner : "-"}</span>
            </div>
            <label>
              {copy("对比运行 ID", "Compare With Run ID")}
              <input value={compareRunId} onChange={(e) => setCompareRunId(e.target.value)} placeholder="run_xxxxx" />
            </label>
            <button
              className="btn"
              disabled={!runId || compareRunId.trim().length === 0 || compareMutation.isPending}
              onClick={() => {
                if (runId && compareRunId.trim()) {
                  compareMutation.mutate({ runA: runId, runB: compareRunId.trim() });
                }
              }}
            >
              {compareMutation.isPending ? copy("对比中...", "Comparing...") : copy("对比", "Compare")}
            </button>
            {compareResult ? (
              <div className="metric-inline">
                <span>{copy("Token 差值", "Token Delta")}: {compareResult.token_delta}</span>
                <span>{copy("耗时差值", "Duration Delta")}: {formatMs(compareResult.duration_delta_ms)}</span>
              </div>
            ) : null}
          </div>
          ) : null}

          {modeVisibility.report ? (
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{copy("全量模拟日志", "Full Simulation Log")}</h3>
              <span className="count-chip">{fullLogResult ? `${fullLogResult.event_count} events` : "source"}</span>
            </div>
            <label>
              {copy("日志格式", "Log Format")}
              <select value={fullLogFormat} onChange={(e) => setFullLogFormat(e.target.value as "markdown" | "json")}>
                <option value="markdown">markdown</option>
                <option value="json">json</option>
              </select>
            </label>
            <button
              className="btn"
              disabled={!runId || fullLogMutation.isPending}
              onClick={() => {
                if (runId) {
                  fullLogMutation.mutate(runId);
                }
              }}
            >
              {fullLogMutation.isPending ? copy("加载中...", "Loading...") : copy("查看全量日志", "Open Full Log")}
            </button>
            {fullLogResult ? (
              <div className="report-output">
                <div className="metric-inline">
                  <span>{fullLogResult.node_count} nodes / {fullLogResult.event_count} events</span>
                  <button
                    className="btn"
                    onClick={() => {
                      const extension = fullLogResult.format === "json" ? "json" : "md";
                      const mime = fullLogResult.format === "json" ? "application/json;charset=utf-8" : "text/markdown;charset=utf-8";
                      const blob = new Blob([fullLogResult.content], { type: mime });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = `${fullLogResult.run_id}-full-log.${extension}`;
                      a.click();
                      URL.revokeObjectURL(url);
                    }}
                  >
                    {copy("下载日志", "Download Log")}
                  </button>
                </div>
                <textarea readOnly rows={10} value={fullLogResult.content} />
              </div>
            ) : null}
          </div>
          ) : null}

          {modeVisibility.report ? (
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("reportGenerator")}</h3>
              <span className="count-chip">{reportResult ? reportResult.mode : "-"}</span>
            </div>
            <label>
              {copy("输出模式", "Output Mode")}
              <select value={reportMode} onChange={(e) => setReportMode(e.target.value as "briefing" | "narrative")}>
                <option value="briefing">{copy("Briefing / 平实汇报", "Briefing / Plain Report")}</option>
                <option value="narrative">{copy("Narrative / 文学化作品", "Narrative / Literary Work")}</option>
              </select>
            </label>
            <label>
              {copy("长度", "Length")}
              <select value={reportLength} onChange={(e) => setReportLength(e.target.value as "short" | "medium" | "long")}>
                <option value="short">short</option>
                <option value="medium">medium</option>
                <option value="long">long</option>
              </select>
            </label>
            <label>
              {copy("标题（可选）", "Title (optional)")}
              <input value={reportTitle} onChange={(e) => setReportTitle(e.target.value)} placeholder={copy("自定义报告标题", "Custom report title")} />
            </label>
            <label>
              {copy("受众（可选）", "Audience (optional)")}
              <input value={reportAudience} onChange={(e) => setReportAudience(e.target.value)} placeholder={copy("受众", "Audience")} />
            </label>
            <label>
              {reportMode === "briefing" ? copy("汇报要求（可选）", "Briefing Requirements (optional)") : copy("文学形式与风格（可选）", "Narrative Form & Style (optional)")}
              <textarea
                value={reportStylePrompt}
                onChange={(e) => setReportStylePrompt(e.target.value)}
                rows={3}
                placeholder={
                  reportMode === "briefing"
                    ? copy("汇报风格", "Briefing style")
                    : copy("文学风格", "Narrative style")
                }
              />
            </label>
            <button
              className="btn"
              disabled={!runId || reportMutation.isPending}
              onClick={() => {
                if (runId) {
                  reportMutation.mutate({ run: runId });
                }
              }}
            >
              {reportMutation.isPending
                ? copy("生成中...", "Generating...")
                : reportMode === "briefing"
                  ? copy("生成汇报", "Generate Briefing")
                  : copy("生成文学作品", "Generate Narrative")}
            </button>
            {reportResult ? (
              <div className="report-output">
                <div className="metric-inline">
                  <span>{reportResult.title}</span>
                  <button
                    className="btn"
                    onClick={() => {
                      const blob = new Blob([reportResult.report_markdown], { type: "text/markdown;charset=utf-8" });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = `${reportResult.run_id}-${reportResult.mode}.md`;
                      a.click();
                      URL.revokeObjectURL(url);
                    }}
                  >
                    {copy("下载 .md", "Download .md")}
                  </button>
                </div>
                <textarea readOnly rows={12} value={reportResult.report_markdown} />
              </div>
            ) : null}
          </div>
          ) : null}

          {modeVisibility.ops ? (
          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("performanceHealth")}</h3>
              <span className="count-chip">{observability?.alerts.length ?? 0} alerts</span>
            </div>
            {observability ? (
              <>
                <div className="metrics-grid">
                  <MetricCard label={copy("1分钟请求", "Req 1m")} value={String(observability.http.requests_1m)} />
                  <MetricCard label="P95 5m" value={formatMs(observability.http.latency_p95_ms_5m)} />
                  <MetricCard label={copy("错误率5分钟", "Error Rate 5m")} value={`${(observability.http.error_rate_5m * 100).toFixed(2)}%`} />
                  <MetricCard label={copy("慢请求5分钟", "Slow Req 5m")} value={String(observability.http.slow_requests_5m)} />
                  <MetricCard label={copy("24h失败运行", "Run Fail 24h")} value={String(observability.runtime.runs_failed_24h)} />
                  <MetricCard label={copy("24h慢节点", "Slow Nodes 24h")} value={String(observability.runtime.slow_nodes_24h)} />
                </div>
                <div className="alert-list">
                  {observability.alerts.length === 0 ? <div className="empty-hint">{copy("暂无性能告警。", "No active performance alerts.")}</div> : null}
                  {observability.alerts.map((a, idx) => (
                    <div key={`${a.code}-${idx}`} className={`alert-item ${a.severity}`}>
                      <strong>{a.code}</strong>
                      <span>{a.message}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="empty-hint">{copy("性能快照不可用。", "Observability snapshot unavailable.")}</div>
            )}
          </div>
          ) : null}

          <div className="analytics-block">
            <div className="sidebar-header logs-header">
              <h3>{tr("runQueue")}</h3>
              <span className="count-chip">{queueStatus ? queueStatus.worker_count : 0} workers</span>
            </div>
            {queueStatus ? (
              <>
                <div className="metrics-grid">
                  <MetricCard label={copy("排队", "Queued")} value={String(queueStatus.queued)} />
                  <MetricCard label={copy("活跃", "Active")} value={String(queueStatus.active)} />
                  <MetricCard label={copy("完成", "Completed")} value={String(queueStatus.completed)} />
                  <MetricCard label={copy("失败", "Failed")} value={String(queueStatus.failed)} />
                  <MetricCard label={copy("平均等待", "Avg Wait")} value={formatMs(queueStatus.avg_wait_ms)} />
                  <MetricCard label={copy("Worker", "Workers")} value={String(queueStatus.worker_count)} />
                </div>
                <div className="metric-inline">
                  <span>{copy("活跃运行", "Active Runs")}:</span>
                  <span>{queueStatus.active_run_ids.length > 0 ? queueStatus.active_run_ids.join(", ") : "-"}</span>
                </div>
              </>
            ) : (
              <div className="empty-hint">{copy("队列快照不可用。", "Queue snapshot unavailable.")}</div>
            )}
          </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

