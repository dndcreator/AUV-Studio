import { create } from "zustand";
import type { Edge, Node } from "reactflow";
import type { NodeFieldSpec, NodeType, NodeSpec, WorkflowDefinition } from "./types";
import { getEntityDefinition, isEntityType, type EntityType } from "./simulationEntities";

type SelectedState = {
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  setSelectedNodeId: (id: string | null) => void;
  setSelectedEdgeId: (id: string | null) => void;
};

type WorkflowState = {
  workflow: WorkflowDefinition;
  nodes: Node[];
  edges: Edge[];
  setWorkflowMeta: (name: string) => void;
  setEnvironmentField: (
    key: "profile" | "scenario" | "time_context" | "spatial_context" | "facts" | "constraints" | "glossary" | "context_book" | "simulation",
    value: unknown
  ) => void;
  setNodes: (nodes: Node[]) => void;
  setEdges: (edges: Edge[]) => void;
  addNode: (nodeType: NodeType, spec?: NodeSpec) => void;
  addEntityNode: (entityType: EntityType) => void;
  addAgentPreset: (role: "planner" | "coder" | "reviewer" | "custom") => void;
  updateNodeConfig: (nodeId: string, key: string, value: unknown) => void;
  updateNodeInput: (nodeId: string, key: string, value: unknown) => void;
  updateEdgeInteraction: (edgeId: string, key: string, value: unknown) => void;
  loadWorkflow: (workflow: WorkflowDefinition) => void;
  buildWorkflowPayload: () => WorkflowDefinition;
};

const defaultWorkflow: WorkflowDefinition = {
  id: "wf_default",
  name: "Untitled Workflow",
  version: 1,
  nodes: [],
  edges: [],
  entry_nodes: [],
  environment: {
    profile: "",
    scenario: "",
    time_context: "",
    spatial_context: "",
    facts: [],
    constraints: [],
    glossary: {},
    context_book: { entries: [], token_budget: 1600 },
    simulation: {}
  }
};

export const useSelectedStore = create<SelectedState>((set) => ({
  selectedNodeId: null,
  selectedEdgeId: null,
  setSelectedNodeId: (id) => set({ selectedNodeId: id, selectedEdgeId: null }),
  setSelectedEdgeId: (id) => set({ selectedEdgeId: id, selectedNodeId: null })
}));

export const useWorkflowStore = create<WorkflowState>((set, get) => ({
  workflow: defaultWorkflow,
  nodes: [],
  edges: [],
  setWorkflowMeta: (name) =>
    set((state) => ({
      workflow: { ...state.workflow, name }
    })),
  setEnvironmentField: (key, value) =>
    set((state) => ({
      workflow: {
        ...state.workflow,
        environment: {
          ...state.workflow.environment,
          [key]: value
        }
      }
    })),
  setNodes: (nodes) => set({ nodes }),
  setEdges: (edges) => set({ edges }),
  addNode: (nodeType, spec) =>
    set((state) => {
      const id = `${nodeType}_${Date.now().toString(36)}_${state.nodes.length}`;
      const node: Node = {
        id,
        type: "default",
        position: { x: 220 + state.nodes.length * 18, y: 120 + state.nodes.length * 12 },
        data: {
          label: `${nodeType.toUpperCase()} ${state.nodes.length + 1}`,
          nodeType,
          config: spec ? defaultsFromFields(spec.config_fields) : defaultConfig(nodeType),
          inputs: spec ? defaultsFromFields(spec.input_fields) : defaultInputs(nodeType)
        }
      };
      return { nodes: [...state.nodes, node] };
    }),
  addEntityNode: (entityType) =>
    set((state) => {
      const def = getEntityDefinition(entityType);
      const id = `${entityType}_${Date.now().toString(36)}_${state.nodes.length}`;
      const config =
        def.nodeType === "human_checkpoint"
          ? {
              ...defaultConfig("human_checkpoint"),
              entity_type: entityType,
              entity_name: def.defaultName,
              entity_profile: def.defaultProfile,
              behavior_prompt: def.behaviorPrompt,
              question_template: "请确认这个模拟是否应该继续，或补充新的方向。"
            }
          : {
              ...defaultConfig("agent"),
              entity_type: entityType,
              entity_name: def.defaultName,
              entity_profile: def.defaultProfile,
              behavior_prompt: def.behaviorPrompt,
              execution_mode: "builtin_llm",
              role: entityType,
              system_prompt: def.behaviorPrompt
            };
      const node: Node = {
        id,
        type: "default",
        className: `entity-flow-node entity-flow-node-${entityType}`,
        position: { x: 220 + state.nodes.length * 18, y: 120 + state.nodes.length * 12 },
        data: {
          label: `${def.titleZh} ${state.nodes.length + 1}`,
          nodeType: def.nodeType,
          config,
          inputs: def.nodeType === "human_checkpoint" ? defaultInputs("human_checkpoint") : defaultInputs("agent")
        }
      };
      return { nodes: [...state.nodes, node] };
    }),
  addAgentPreset: (role) =>
    set((state) => {
      const id = `agent_${Date.now().toString(36)}_${state.nodes.length}`;
      const node: Node = {
        id,
        type: "default",
        position: { x: 220 + state.nodes.length * 18, y: 120 + state.nodes.length * 12 },
        data: {
          label: `${role.toUpperCase()} ${state.nodes.length + 1}`,
          nodeType: "agent",
          config: {
            ...defaultConfig("agent"),
            role,
            system_prompt:
              role === "planner"
                ? "You are a planning agent. Break goals into clear executable steps."
                : role === "coder"
                  ? "You are an execution agent. Produce concrete implementation-quality output."
                  : role === "reviewer"
                    ? "You are a reviewer. Critique risks, assumptions, and quality."
                    : "You are a helpful specialist agent."
          },
          inputs: defaultInputs("agent")
        }
      };
      return { nodes: [...state.nodes, node] };
    }),
  updateNodeConfig: (nodeId, key, value) =>
    set((state) => ({
      nodes: state.nodes.map((n) =>
        n.id === nodeId
          ? {
              ...n,
              data: {
                ...n.data,
                config: {
                  ...((n.data?.config as Record<string, unknown>) ?? {}),
                  [key]: value
                }
              }
            }
          : n
      )
    })),
  updateNodeInput: (nodeId, key, value) =>
    set((state) => ({
      nodes: state.nodes.map((n) =>
        n.id === nodeId
          ? {
              ...n,
              data: {
                ...n.data,
                inputs: {
                  ...((n.data?.inputs as Record<string, unknown>) ?? {}),
                  [key]: value
                }
              }
            }
          : n
      )
    })),
  updateEdgeInteraction: (edgeId, key, value) =>
    set((state) => ({
      edges: state.edges.map((e) => {
        if (e.id !== edgeId) {
          return e;
        }
        const cur = ((e.data as Record<string, unknown>)?.interaction ?? defaultEdgeInteraction()) as Record<string, unknown>;
        return {
          ...e,
          data: {
            ...((e.data as Record<string, unknown>) ?? {}),
            interaction: {
              ...cur,
              [key]: value
            }
          }
        };
      })
    })),
  loadWorkflow: (workflow) => {
    const normalizedWorkflow: WorkflowDefinition = {
      ...workflow,
      environment: workflow.environment ?? {
        profile: "",
        scenario: "",
        time_context: "",
        spatial_context: "",
        facts: [],
        constraints: [],
        glossary: {},
        context_book: { entries: [], token_budget: 1600 },
        simulation: {}
      }
    };
    normalizedWorkflow.environment = {
      profile: normalizedWorkflow.environment.profile ?? "",
      scenario: normalizedWorkflow.environment.scenario ?? "",
      time_context: normalizedWorkflow.environment.time_context ?? "",
      spatial_context: normalizedWorkflow.environment.spatial_context ?? "",
      facts: normalizedWorkflow.environment.facts ?? [],
      constraints: normalizedWorkflow.environment.constraints ?? [],
      glossary: normalizedWorkflow.environment.glossary ?? {},
      context_book: normalizeContextBook(normalizedWorkflow.environment.context_book),
      simulation: normalizedWorkflow.environment.simulation ?? {}
    };
    const nodes: Node[] = workflow.nodes.map((node) => {
      const entityType = node.config?.entity_type;
      const hasEntityType = isEntityType(entityType);
      const entityDef = hasEntityType ? getEntityDefinition(entityType) : null;
      return {
        id: node.id,
        type: "default",
        className: hasEntityType ? `entity-flow-node entity-flow-node-${entityType}` : undefined,
        position: node.position,
        data: {
          label: hasEntityType
            ? String(node.config.entity_name || `${entityDef?.titleZh ?? node.type} ${node.id}`)
            : `${node.type.toUpperCase()} ${node.id}`,
          nodeType: node.type,
          config: node.config,
          inputs: node.inputs
        }
      };
    });
    const edges: Edge[] = workflow.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.condition ?? undefined,
      data: {
        interaction: e.interaction ?? defaultEdgeInteraction()
      }
    }));
    set({ workflow: normalizedWorkflow, nodes, edges });
  },
  buildWorkflowPayload: () => {
    const state = get();
    const nodes = state.nodes.map((n) => ({
      id: n.id,
      type: String((n.data as Record<string, unknown>)?.nodeType ?? "prompt") as NodeType,
      position: n.position,
      config: ((n.data as Record<string, unknown>)?.config ?? {}) as Record<string, unknown>,
      inputs: ((n.data as Record<string, unknown>)?.inputs ?? {}) as Record<string, unknown>
    }));
    const normalizedNodes = nodes.map((n) => ({
      ...n,
      config: normalizeRecordValues(n.config),
      inputs: normalizeRecordValues(n.inputs)
    }));
    const edges = state.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      condition: e.label ? String(e.label) : null,
      interaction: (((e.data as Record<string, unknown>)?.interaction ?? defaultEdgeInteraction()) as Record<string, unknown>) as WorkflowDefinition["edges"][number]["interaction"]
    }));
    const entry = normalizedNodes
      .map((n) => n.id)
      .filter((id) => !edges.some((e) => e.target === id));
    return {
      ...state.workflow,
      nodes: normalizedNodes,
      edges,
      entry_nodes: entry,
      environment: normalizeEnvironment(state.workflow.environment)
    };
  }
}));

function defaultConfig(type: NodeType): Record<string, unknown> {
  if (type === "external_agent") {
    return {
      integration_mode: "mock",
      agent_id: "user_agent",
      endpoint_url: "",
      endpoint_path: "/agent/tasks",
      api_key: "",
      timeout_ms: 60000,
      max_tokens: 1500,
      include_background_in_prompt: "true",
      prompt_template: "{{prompt}}"
    };
  }
  if (type === "director") {
    return {
      model: "",
      objective: "Keep collaboration aligned with scenario goals while controlling cost.",
      style_guardrails: "Be concise and role-consistent.",
      gpro_candidates: 3
    };
  }
  if (type === "agent") {
    return {
      entity_type: "individual",
      entity_name: "",
      entity_profile: "",
      behavior_prompt: "You are simulating one concrete entity in the world.",
      execution_mode: "builtin_llm",
      role: "individual",
      model: "",
      model_connection_mode: "platform_default",
      system_prompt: "You are simulating one concrete entity in the world.",
      external_endpoint_url: "",
      external_integration_mode: "mock",
      external_endpoint_path: "/agent/tasks",
      external_api_key: "",
      external_timeout_ms: 60000
    };
  }
  if (type === "prompt") {
    return { template: "Process input: {{input.task}}" };
  }
  if (type === "human_checkpoint") {
    return {
      owner: "director",
      question_template: "Please review current simulation state and provide intervention guidance.",
      required: "true"
    };
  }
  if (type === "tool") {
    return { tool_name: "echo" };
  }
  return { expression: "True" };
}

function defaultInputs(type: NodeType): Record<string, unknown> {
  if (type === "external_agent") {
    return { prompt: "{{input.task}}" };
  }
  if (type === "director") {
    return { situation: "{{input.task}}" };
  }
  if (type === "agent") {
    return { prompt: "{{input.task}}" };
  }
  if (type === "human_checkpoint") {
    return { question: "", context_hint: "{{input.task}}" };
  }
  if (type === "tool") {
    return { text: "{{input.task}}" };
  }
  return {};
}

function defaultsFromFields(fields: NodeFieldSpec[]): Record<string, unknown> {
  return fields.reduce<Record<string, unknown>>((acc, f) => {
    acc[f.key] = f.default;
    return acc;
  }, {});
}

function normalizeRecordValues(record: Record<string, unknown>): Record<string, unknown> {
  return Object.entries(record).reduce<Record<string, unknown>>((acc, [k, v]) => {
    acc[k] = tryParseJson(v);
    return acc;
  }, {});
}

function tryParseJson(value: unknown): unknown {
  if (typeof value !== "string") {
    return value;
  }
  const t = value.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) {
    return value;
  }
  try {
    return JSON.parse(t);
  } catch {
    return value;
  }
}

export function defaultEdgeInteraction() {
  return {
    mode: "dialogue",
    relation: "peer",
    template: "",
    required: false,
    intensity: 1
  };
}

function normalizeEnvironment(env: WorkflowDefinition["environment"]): WorkflowDefinition["environment"] {
  return {
    profile: String(env.profile ?? ""),
    scenario: String(env.scenario ?? ""),
    time_context: String(env.time_context ?? ""),
    spatial_context: String(env.spatial_context ?? ""),
    facts: normalizeStringList(env.facts),
    constraints: normalizeStringList(env.constraints),
    glossary: normalizeGlossary(env.glossary),
    context_book: normalizeContextBook(env.context_book),
    simulation: normalizeSimulation(env.simulation)
  };
}

function normalizeContextBook(value: WorkflowDefinition["environment"]["context_book"]): NonNullable<WorkflowDefinition["environment"]["context_book"]> {
  const entries = Array.isArray(value?.entries)
    ? value.entries.filter((entry) => entry && typeof entry.content === "string" && entry.content.trim().length > 0)
    : [];
  return {
    entries,
    token_budget: Math.max(128, Math.min(32000, Number(value?.token_budget ?? 1600)))
  };
}

function normalizeStringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((v) => String(v)).filter((v) => v.trim().length > 0);
  }
  if (typeof value === "string") {
    return value
      .split("\n")
      .map((v) => v.trim())
      .filter((v) => v.length > 0);
  }
  return [];
}

function normalizeGlossary(value: unknown): Record<string, string> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return Object.entries(value as Record<string, unknown>).reduce<Record<string, string>>((acc, [k, v]) => {
      acc[String(k)] = String(v);
      return acc;
    }, {});
  }
  if (typeof value === "string") {
    try {
      const obj = JSON.parse(value) as Record<string, unknown>;
      return Object.entries(obj).reduce<Record<string, string>>((acc, [k, v]) => {
        acc[String(k)] = String(v);
        return acc;
      }, {});
    } catch {
      return {};
    }
  }
  return {};
}

function normalizeSimulation(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value === "string") {
    try {
      const obj = JSON.parse(value);
      if (obj && typeof obj === "object" && !Array.isArray(obj)) {
        return obj as Record<string, unknown>;
      }
    } catch {
      return {};
    }
  }
  return {};
}
