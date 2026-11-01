import { describe, expect, it } from "vitest";
import { buildSceneFrame, publicActionText, readPresentation } from "./presentation";
import type { RunEvent, WorkflowDefinition } from "../types";

const workflow: WorkflowDefinition = { id: "wf", name: "Scene", version: 1, entry_nodes: [],
  nodes: [{ id: "a", type: "agent", position: { x: 30, y: 40 }, inputs: {}, config: { entity_name: "A", entity_type: "individual" } },
    { id: "summary_1", type: "agent", position: { x: 0, y: 0 }, inputs: {}, config: {} }], edges: [],
  environment: { profile: "", scenario: "", facts: [], constraints: [], glossary: {}, simulation: {} } };
const event = (seq: number, output: Record<string, unknown>): RunEvent => ({ seq, node_id: "a", run_id: "r", event: "succeeded", timestamp: "2026-10-31T00:00:00Z", payload: { output } });

describe("read-only scene projection", () => {
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
