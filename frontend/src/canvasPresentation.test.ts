import { describe, expect, it } from "vitest";
import type { Node } from "reactflow";
import type { RunEvent } from "./types";
import { presentCanvasNodes } from "./canvasPresentation";

const nodes: Node[] = [
  { id: "a", position: { x: 10, y: 20 }, className: "entity-flow-node entity-flow-node-individual", data: { label: "Lin", config: { entity_type: "individual" } } },
  { id: "b", position: { x: 30, y: 40 }, data: { label: "Custom" } },
];
const event = (seq: number, state: RunEvent["event"]): RunEvent => ({ seq, run_id: "run_a", node_id: "a", event: state, timestamp: "2026-10-21T00:00:00Z", payload: {} });

describe("canvas presentation", () => {
  it("uses the latest node event without mutating the editable workflow", () => {
    const result = presentCanvasNodes(nodes, [event(4, "succeeded"), event(1, "queued"), event(3, "running"), event(5, "director_corrected")]);
    expect(result[0].data.displayStatus).toBe("succeeded");
    expect(result[0].className).toContain("entity-flow-node-individual canvas-node-succeeded");
    expect(result[0].position).toEqual(nodes[0].position);
    expect(result[1].data.displayStatus).toBe("idle");
    expect(nodes[0].data).not.toHaveProperty("displayStatus");
    expect(nodes[0].className).not.toContain("canvas-node");
  });

  it("follows replay's visible event range and resets cleanly for a new run", () => {
    const events = [event(1, "running"), event(2, "waiting_human"), event(3, "failed")];
    expect(presentCanvasNodes(nodes, events.slice(0, 1))[0].data.displayStatus).toBe("running");
    expect(presentCanvasNodes(nodes, events.slice(0, 2))[0].data.displayStatus).toBe("waiting_human");
    expect(presentCanvasNodes(nodes, events)[0].data.displayStatus).toBe("failed");
    expect(presentCanvasNodes(nodes, [])[0].data.displayStatus).toBe("idle");
  });

  it("keeps the node running while candidate consistency checks are recorded", () => {
    const result = presentCanvasNodes(nodes, [event(1, "running"), event(2, "action_checked")]);
    expect(result[0].data.displayStatus).toBe("running");
  });
});
