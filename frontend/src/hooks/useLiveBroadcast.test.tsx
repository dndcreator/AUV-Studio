import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Node } from "reactflow";
import type { RunEvent } from "../types";
import { useLiveBroadcast } from "./useLiveBroadcast";

const nodes: Node[] = [
  {
    id: "agent_1",
    position: { x: 0, y: 0 },
    data: { label: "调查员", config: { entity_name: "林岚" } },
  },
];

const events: RunEvent[] = [
  {
    run_id: "run_1",
    node_id: "agent_1",
    event: "succeeded",
    timestamp: "2026-10-17T10:00:00.000Z",
    seq: 1,
    payload: { output: { content: "林岚把记录放到桌上，指出样本存在偏差。" } },
  },
];

describe("useLiveBroadcast", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("paces readable events and resets the broadcast cursor", () => {
    const { result, rerender } = renderHook(
      ({ runEvents }: { runEvents: RunEvent[] }) => useLiveBroadcast(runEvents, nodes, "zh-CN", "research", "news"),
      { initialProps: { runEvents: events } },
    );

    expect(result.current.snapshot).toEqual([]);
    act(() => vi.advanceTimersByTime(6000));

    expect(result.current.current?.speaker).toBe("林岚");
    expect(result.current.current?.text).toContain("样本存在偏差");
    expect(result.current.broadcastRows).toHaveLength(1);

    act(() => result.current.reset());
    expect(result.current.snapshot).toEqual([]);

    rerender({
      runEvents: [
        ...events,
        {
          ...events[0],
          seq: 2,
          timestamp: "2026-10-17T10:01:00.000Z",
          payload: { output: { content: "她随后提交了修正后的样本方案。" } },
        },
      ],
    });
    act(() => vi.advanceTimersByTime(6000));
    expect(result.current.snapshot).toHaveLength(1);
  });

  it("uses a readable fallback name and clears stale broadcasts when the event range resets", () => {
    const fallbackNodes = [{ ...nodes[0], data: { label: "调查员", config: { entity_name: "", profile: "A long persona description" } } }];
    const { result, rerender } = renderHook(({ rows }: { rows: RunEvent[] }) => useLiveBroadcast(rows, fallbackNodes, "zh-CN", "research", "news"),
      { initialProps: { rows: events } });
    act(() => vi.advanceTimersByTime(6000));
    expect(result.current.current?.speaker).toBe("调查员");
    rerender({ rows: [] });
    expect(result.current.snapshot).toEqual([]);
    expect(result.current.current).toBeNull();
  });
});
