import { describe, expect, it } from "vitest";
import { buildGlimpseRows, pickAcceptedGlimpse, scoreGlimpseRow } from "./glimpse";
import type { RunEvent } from "./types";

function event(partial: Partial<RunEvent>): RunEvent {
  return {
    run_id: "run_1",
    node_id: "agent_1",
    event: "succeeded",
    timestamp: "2026-08-11T10:00:00.000Z",
    payload: {},
    ...partial
  };
}

describe("Live broadcast selection", () => {
  it("ignores lifecycle noise and emits readable content only", () => {
    const rows = buildGlimpseRows([
      event({ seq: 1, event: "queued" }),
      event({ seq: 2, event: "running" }),
      event({ seq: 3, payload: { output: { content: "母亲把手机推到桌边：先吃饭。" } } }),
      event({ seq: 4, node_id: "report_1", payload: { output: { text: "duplicate report" } } })
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0].text).toBe("母亲把手机推到桌边：先吃饭。");
  });

  it("does not expose raw provider JSON", () => {
    const rows = buildGlimpseRows([event({ payload: { output: { content: '{"raw":{"choices":[]}}' } } })]);
    expect(rows).toHaveLength(0);
  });

  it("includes readable output from custom and external node ids", () => {
    const rows = buildGlimpseRows([
      event({ node_id: "custom_partner", payload: { output: { content: "她沉默了一会儿，然后把门打开。" } } })
    ]);
    expect(rows[0].speaker).toBe("custom_partner");
    expect(rows[0].text).toContain("把门打开");
  });

  it("keeps final synthesis out of the live scene", () => {
    const rows = buildGlimpseRows([
      event({ node_id: "summary_1", payload: { output: { content: "A very long final synthesis" } } })
    ]);
    expect(rows).toHaveLength(0);
  });

  it("keeps user-facing control events but hides backstage director corrections", () => {
    const rows = buildGlimpseRows([
      event({ seq: 1, event: "director_corrected", payload: { guidance: "当前剧情过于平淡，增加一个可追溯的转折。" } }),
      event({ seq: 2, event: "director_overridden", payload: { command_text: "让故事进入毕业季。" } }),
      event({ seq: 3, event: "waiting_human", payload: { output: { question: "是否继续当前方向？" } } })
    ]);
    expect(rows.map((row) => row.event)).toEqual(["director_overridden", "waiting_human"]);
  });
});

describe("Live broadcast cadence", () => {
  it("prioritizes critical rows immediately", () => {
    expect(scoreGlimpseRow("failed", "critical failure")).toBeGreaterThanOrEqual(90);
    const picked = pickAcceptedGlimpse([{ seq: 1, speaker: "Director", event: "failed", text: "failed", time: "10:00", score: 98 }], 100);
    expect(picked?.seq).toBe(1);
  });

  it("rate-limits ordinary content", () => {
    const rows = [{ seq: 2, speaker: "agent_1", event: "content", text: "ordinary update", time: "10:01", score: 72 }];
    expect(pickAcceptedGlimpse(rows, 2000)).toBeNull();
    expect(pickAcceptedGlimpse(rows, 6000)?.seq).toBe(2);
  });
});
