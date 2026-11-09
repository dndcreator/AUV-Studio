import { describe, expect, it } from "vitest";
import { buildSceneFrame, publicActionText, readPresentation } from "./presentation";
import type { RunEvent, WorkflowDefinition } from "../types";

const workflow: WorkflowDefinition = { id: "wf", name: "Scene", version: 1, entry_nodes: [],
  nodes: [{ id: "a", type: "agent", position: { x: 30, y: 40 }, inputs: {}, config: { entity_name: "A", entity_type: "individual" } },
    { id: "summary_1", type: "agent", position: { x: 0, y: 0 }, inputs: {}, config: {} }], edges: [],
  environment: { profile: "", scenario: "", facts: [], constraints: [], glossary: {}, simulation: {} } };
const event = (seq: number, output: Record<string, unknown>): RunEvent => ({ seq, node_id: "a", run_id: "r", event: "succeeded", timestamp: "2026-10-31T00:00:00Z", payload: { output } });

describe("read-only scene projection", () => {
  it("restores inherited public bindings and role control without replaying future or private facts", () => {
    const wf = structuredClone(workflow);
    wf.environment.simulation = { presentation: { version: 1, renderer: "spatial",
      regions: [{ id: "bridge", label: "Bridge" }, { id: "engine", label: "Engine" }],
      placements: [{ node_id: "a", region_id: "bridge" }],
      bindings: [{ kind: "location", concept_id: "position", target_id: "a", values: { engine: "engine" } }] } };
    const restored: RunEvent = { ...event(20, {}), node_id: "run_control", event: "state_restored", payload: {
      source_run_id: "old", checkpoint_seq: 10, source_event_hash: "a".repeat(64), player_controls: { a: "player_controlled" },
      public_actions: [{ node_id: "a", event_seq: 5, action: "I reach engineering." }], dynamic_state: {
        concepts: { position: { status: "active", visibility: "global", subject_id: "a", value_type: "text" } },
        values: { position: { value: "engine", evidence_kind: "player_outcome", source_refs: [{ event_type: "player_outcome", event_seq: 9 }] } },
      },
    } };
    const frame = buildSceneFrame(wf, [restored]);
    expect(frame.presentation.placements[0].region_id).toBe("engine");
    expect(frame.actors[0].action).toBe("I reach engineering.");
    expect(frame.actors[0].control).toBe("player_controlled");
    const future = { ...event(21, {}), event: "player_control_changed" as const, payload: { actor_id: "a", control: "autonomous" } };
    expect(buildSceneFrame(wf, [restored, future]).actors[0].control).toBe("autonomous");
    expect(buildSceneFrame(wf, []).presentation.placements[0].region_id).toBe("bridge");
    const invalid = structuredClone(restored); invalid.payload.source_event_hash = "bad";
    expect(buildSceneFrame(wf, [invalid]).presentation.placements[0].region_id).toBe("bridge");
    const hidden = structuredClone(restored);
    (hidden.payload.dynamic_state as { concepts: { position: { visibility: string } } }).concepts.position.visibility = "private";
    expect(buildSceneFrame(wf, [hidden]).presentation.placements[0].region_id).toBe("bridge");
  });
  it("projects a public player outcome only with its real source event, and never a secret", () => {
    const wf = structuredClone(workflow);
    wf.environment.simulation = { presentation: { version: 1, renderer: "spatial",
      regions: [{ id: "hall", label: "Hall" }, { id: "cell", label: "Cell" }],
      placements: [{ node_id: "a", region_id: "hall", x: 20, y: 40 }],
      bindings: [{ kind: "location", concept_id: "position", target_id: "a", values: { cell: "cell" } }] } };
    const source: RunEvent = { ...event(3, {}), node_id: "player", event: "player_outcome", payload: { instruction: "Move A." } };
    const state: RunEvent = { ...event(4, {}), node_id: "state_coder", event: "dynamic_state_updated", payload: {
      dynamic_state: { concepts: { position: { status: "active", visibility: "global", subject_id: "a" } },
        values: { position: { value: "cell", evidence_kind: "player_outcome", source_refs: [{ event_type: "player_outcome", event_seq: 3 }] } } } } };
    expect(buildSceneFrame(wf, [source, state]).presentation.placements[0].region_id).toBe("cell");
    expect(buildSceneFrame(wf, [state]).presentation.placements[0].region_id).toBe("hall");
    expect(buildSceneFrame(wf, [source]).presentation.placements[0].region_id).toBe("hall");
    expect(buildSceneFrame(wf, [{ ...source, event: "player_requested" }, state]).presentation.placements[0].region_id).toBe("hall");
    const hidden = structuredClone(state);
    (hidden.payload.dynamic_state as { concepts: { position: { visibility: string } } }).concepts.position.visibility = "private";
    expect(buildSceneFrame(wf, [source, hidden]).presentation.placements[0].region_id).toBe("hall");
  });
  it("preserves valid axis-aligned SVG line geometry", () => {
    const result = readPresentation({ version: 1, primitives: [
      { id: "vertical", shape: "line", width: 0, height: 100, color: "#FF8800" },
      { id: "horizontal", shape: "line", width: 100, height: 0, color: "#FF8800" },
    ] }, new Set());
    expect(result.primitives?.[0].width).toBe(0);
    expect(result.primitives?.[1].height).toBe(0);
  });
  it("moves and recolors only from committed public action-backed state, and replays exactly", () => {
    const wf = structuredClone(workflow);
    wf.environment.simulation = { presentation: { version: 1, renderer: "spatial",
      regions: [{ id: "bridge", label: "Bridge" }, { id: "engine", label: "Engine" }],
      placements: [{ node_id: "a", region_id: "bridge", x: 25, y: 60 }],
      primitives: [{ id: "lamp", shape: "circle", color: "#00FF00" }],
      bindings: [{ kind: "location", concept_id: "position", target_id: "a", values: { engine: "engine" } },
        { kind: "color", concept_id: "power", target_id: "lamp", values: { lost: "#FF0000" } }] } };
    const source = event(3, { action: "A enters engineering and cuts the power." });
    const state: RunEvent = { ...source, seq: 4, node_id: "state_coder", event: "dynamic_state_updated", payload: {
      coding: { status: "coded" }, dynamic_state: {
        concepts: { position: { status: "active", visibility: "global", subject_id: "a" }, power: { status: "active", visibility: "global" } },
        values: Object.fromEntries([["position", "engine"], ["power", "lost"]].map(([id, value]) => [id, { value, evidence_kind: "action", source_refs: [{ node_id: "a", action_index: 1, event_seq: 3 }] }])) } } };
    const frame = buildSceneFrame(wf, [source, state]);
    expect(frame.presentation.placements[0].region_id).toBe("engine");
    expect(frame.presentation.primitives?.[0].color).toBe("#FF0000");
    expect(buildSceneFrame(wf, [source]).presentation.placements[0].region_id).toBe("bridge");
    for (const invalid of ["private", "claim", "pending", "missing_source"]) {
      const changed = structuredClone(state);
      const payload = changed.payload as typeof state.payload;
      if (invalid === "pending") payload.coding = { status: "pending" };
      const dynamic = payload.dynamic_state as { concepts: Record<string, Record<string, unknown>>; values: Record<string, Record<string, unknown>> };
      if (invalid === "private") dynamic.concepts.position.visibility = "private";
      if (invalid === "claim") dynamic.values.position.evidence_kind = "claim";
      expect(buildSceneFrame(wf, invalid === "missing_source" ? [changed] : [source, changed]).presentation.placements[0].region_id).toBe("bridge");
    }
  });
  it("renders only public actions, never raw envelopes or private cognition", () => {
    const events = [event(2, { action: "A opens the door.", intent: "PRIVATE PLAN", observation: "PRIVATE KNOWLEDGE", raw: { secret: true } }),
      event(1, { action: "An older action." }), event(3, { content: '{"intent":"SECRET","raw":{}}' })];
    const original = JSON.stringify({ workflow, events });
    const frame = buildSceneFrame(workflow, events);
    expect(frame.actors).toHaveLength(1);
    expect(frame.actors[0].action).toBe("A opens the door.");
    expect(JSON.stringify(frame)).not.toMatch(/PRIVATE|SECRET|secret/);
    expect(JSON.stringify({ workflow, events })).toBe(original);
    expect(publicActionText(event(4, { content: '{"action":"A waits.","intent":"SECRET"}' }))).toBe("A waits.");
  });

  it("rejects executable styling, unknown placements, duplicate regions and remote image URLs", () => {
    const result = readPresentation({ version: 1, renderer: "spatial", theme: { accent: "url(javascript:evil)", ink: "#f0f0f0", extra: "red" },
      regions: [{ id: "bridge", label: "Bridge", width: 450, height: 300 }, { id: "bridge" }],
      placements: [{ node_id: "a", region_id: "bridge", x: 30, y: 70 }, { node_id: "other", region_id: "bridge" }, { node_id: "a" }],
      assets: [{ kind: "background", url: "https://tracking.invalid/pixel" }, { kind: "portrait", node_id: "a", url: "/api/media/generations/g1/assets/a1/content" }] }, new Set(["a"]));
    expect(result.theme).toEqual({ ink: "#f0f0f0" });
    expect(result.regions).toHaveLength(1);
    expect(result.placements).toEqual([{ node_id: "a", region_id: "bridge", x: 30, y: 70 }]);
    expect(result.assets).toHaveLength(1);
    expect(result.renderer).toBe("spatial");
  });

  it("falls back on missing/unknown versions and keeps plugin identifiers declarative", () => {
    expect(readPresentation({ version: 2, renderer: "spatial" }, new Set()).renderer).toBe("network");
    expect(readPresentation({ version: 1, renderer: "spatial" }, new Set()).renderer).toBe("network");
    expect(readPresentation({ version: 1, renderer: "ship-deck" }, new Set()).renderer).toBe("ship-deck");
    expect(readPresentation({ version: 1, renderer: "javascript:evil" }, new Set()).renderer).toBe("network");
  });
});
