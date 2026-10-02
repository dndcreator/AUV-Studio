import { describe, expect, it, beforeEach } from "vitest";
import { useWorkflowStore } from "./store";
import type { WorkflowDefinition } from "./types";

const baseWorkflow: WorkflowDefinition = {
  id: "wf_test",
  name: "Test Workflow",
  version: 1,
  nodes: [],
  edges: [],
  entry_nodes: [],
  environment: {
    profile: "",
    scenario: "",
    facts: [],
    constraints: [],
    glossary: {}
  }
};

describe("workflow store", () => {
  beforeEach(() => {
    useWorkflowStore.setState({
      workflow: structuredClone(baseWorkflow),
      nodes: [],
      edges: []
    });
  });

  it("creates human_checkpoint node with expected defaults", () => {
    const store = useWorkflowStore.getState();
    store.addNode("human_checkpoint");
    const next = useWorkflowStore.getState();
    expect(next.nodes).toHaveLength(1);
    const nodeData = next.nodes[0].data as Record<string, unknown>;
    expect(nodeData.nodeType).toBe("human_checkpoint");
    expect((nodeData.config as Record<string, unknown>).owner).toBe("director");
    expect((nodeData.inputs as Record<string, unknown>).context_hint).toBe("{{input.task}}");
  });

  it("creates director node with governance defaults", () => {
    const store = useWorkflowStore.getState();
    store.addNode("director");
    const next = useWorkflowStore.getState();
    const nodeData = next.nodes[0].data as Record<string, unknown>;
    expect(nodeData.nodeType).toBe("director");
    const config = nodeData.config as Record<string, unknown>;
    expect(config.model).toBe("");
    expect(config.gpro_candidates).toBe(3);
    expect(typeof config.objective).toBe("string");
  });

  it("creates world entity nodes as compatible agent nodes with behavior rules", () => {
    const store = useWorkflowStore.getState();
    store.addEntityNode("group");
    const next = useWorkflowStore.getState();
    expect(next.nodes).toHaveLength(1);
    const nodeData = next.nodes[0].data as Record<string, unknown>;
    expect(nodeData.nodeType).toBe("agent");
    expect(nodeData.label).toContain("群体");
    const config = nodeData.config as Record<string, unknown>;
    expect(config.entity_type).toBe("group");
    expect(config.execution_mode).toBe("builtin_llm");
    expect(String(config.behavior_prompt)).toContain("simulating a group");
    expect(String(config.behavior_prompt)).toContain("not a single person");
    expect(String(config.behavior_prompt)).toContain("rumor spreads");
  });

  it("normalizes environment fields into payload", () => {
    const store = useWorkflowStore.getState();
    store.setEnvironmentField("facts", "f1\nf2\n");
    store.setEnvironmentField("constraints", ["c1", "c2"]);
    store.setEnvironmentField("glossary", "{\"KPI\":\"Key performance indicator\"}");
    store.setEnvironmentField("time_context", "day 3");
    store.setEnvironmentField("spatial_context", "room A");
    store.setEnvironmentField("context_book", {
      entries: [
        {
          id: "entry_1",
          title: "Secret",
          content: "Only agent A knows.",
          kind: "knowledge",
          visibility: { scope: "private", node_ids: ["a"] },
          activation: { always: true, keywords: [], semantic_hint: "", related_entry_ids: [] },
          priority: 50,
          source: "user",
          enabled: true
        }
      ],
      token_budget: 900
    });

    const payload = useWorkflowStore.getState().buildWorkflowPayload();
    expect(payload.environment.facts).toEqual(["f1", "f2"]);
    expect(payload.environment.constraints).toEqual(["c1", "c2"]);
    expect(payload.environment.glossary).toEqual({ KPI: "Key performance indicator" });
    expect(payload.environment.time_context).toBe("day 3");
    expect(payload.environment.spatial_context).toBe("room A");
    expect(payload.environment.context_book?.entries[0].visibility.node_ids).toEqual(["a"]);
    expect(payload.environment.context_book?.token_budget).toBe(900);
  });

  it("persists edge interaction in workflow payload", () => {
    useWorkflowStore.setState({
      workflow: structuredClone(baseWorkflow),
      nodes: [
        {
          id: "a",
          type: "default",
          position: { x: 0, y: 0 },
          data: { nodeType: "prompt", config: { template: "A" }, inputs: {} }
        },
        {
          id: "b",
          type: "default",
          position: { x: 100, y: 0 },
          data: { nodeType: "agent", config: { role: "planner" }, inputs: {} }
        }
      ],
      edges: [
        {
          id: "e1",
          source: "a",
          target: "b",
          label: null,
          data: {
            interaction: {
              mode: "instruction",
              relation: "leader",
              template: "do it",
              required: true,
              intensity: 2
            }
          }
        }
      ]
    });

    const payload = useWorkflowStore.getState().buildWorkflowPayload();
    expect(payload.edges).toHaveLength(1);
    expect(payload.edges[0].interaction).toEqual({
      mode: "instruction",
      relation: "leader",
      template: "do it",
      required: true,
      intensity: 2
    });
  });
});
