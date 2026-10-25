import { describe, expect, it } from "vitest";
import { broadcastDelay } from "./livePlayback";
import type { RunEvent } from "./types";

const events = (gap: number): RunEvent[] => [0, 1, 2, 3].map((index) => ({ seq: index + 1, run_id: "r", node_id: "a",
  event: "succeeded", timestamp: new Date(1000 + index * gap).toISOString(), payload: {} }));

describe("broadcast cadence", () => {
  it("adapts to actual arrival cadence and speeds up when a backlog builds", () => {
    expect(broadcastDelay(events(8000), 1)).toBeGreaterThan(broadcastDelay(events(2000), 1));
    expect(broadcastDelay(events(8000), 9)).toBeLessThan(broadcastDelay(events(8000), 1));
    expect(broadcastDelay(events(1), 100)).toBe(1200);
    expect(broadcastDelay(events(60_000), 1)).toBe(10_000);
  });
});
