import { describe, expect, it } from "vitest";
import { buildSimulationView, formatDynamicStateValue, monitorMessage } from "./appSupport";
import type { RunEvent } from "./types";

describe("dynamic state monitoring", () => {
  it("reads globally visible concepts from state-coder events", () => {
    const events: RunEvent[] = [
      {
        seq: 1,
        run_id: "run_1",
        node_id: "agent_1",
        event: "succeeded",
        timestamp: "2026-10-04T00:00:00Z",
        payload: {
          simulation_state: {
            current_phase_index: 0,
            phases: [{ name: "Discovery" }],
            variables: { progress: 0.25, confidence: 0.5, risk: 0.2, alignment: 0.7 }
          }
        }
      },
      {
        seq: 2,
        run_id: "run_1",
        node_id: "state_coder",
        event: "dynamic_state_updated",
        timestamp: "2026-10-04T00:00:01Z",
        payload: {
          changed: true,
          active_concepts: 1,
          dynamic_state: {
            enabled: true,
            version: 2,
            schema_version: 1,
            concepts: { unresolved_bias: { description: "Bias remains unresolved." } },
            values: { unresolved_bias: { value: "active", confidence: 0.8 } }
          }
        }
      }
    ];

    const view = buildSimulationView(events);
    expect(view.dynamicState.enabled).toBe(true);
    expect(view.dynamicState.concepts[0]).toMatchObject({ id: "unresolved_bias", value: "active", confidence: 0.8 });
    expect(monitorMessage(events[1])).toContain("1 active concepts");
  });

  it("formats structured values without raw JSON", () => {
    expect(formatDynamicStateValue({ status: "open", owners: ["a", "b"] })).toBe("status: open · owners: a · b");
  });
});
