import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { RunEvent } from "../types";
import { mergeRunEvents, useRunPolling } from "./useRunPolling";

const event = (seq: number): RunEvent => ({ run_id: "run_a", node_id: "a", seq, event: "succeeded", timestamp: "2026-10-22T00:00:00Z", payload: {} });

describe("run polling", () => {
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

  it("merges event pages once and preserves event order", () => {
    const previous = [event(1), event(2)];
    const merged = mergeRunEvents(previous, [event(3), event(2), event(3)]);
    expect(merged.map((row) => row.seq)).toEqual([1, 2, 3]);
    expect(previous).toHaveLength(2);
    expect(mergeRunEvents(previous, [event(1)])).toBe(previous);
  });

  it("drains the last event pages before publishing a completed status", async () => {
    vi.spyOn(api, "getRunStatus").mockResolvedValue({ run_id: "run_a", status: "succeeded" });
    const events = vi.spyOn(api, "getRunEvents")
      .mockResolvedValueOnce({ run_id: "run_a", events: Array.from({ length: 500 }, (_, i) => event(i + 1)) })
      .mockResolvedValueOnce({ run_id: "run_a", events: [event(501)] });
    vi.spyOn(api, "getRunMetrics").mockRejectedValue(new Error("metrics unavailable"));
    const receive = vi.fn();
    const { result } = renderHook(() => useRunPolling("run_a", "waiting_human", receive));
    await act(async () => { await result.current(); });
    expect(events.mock.calls.map((args) => args[1])).toEqual([0, 500]);
    expect(receive.mock.calls[0][0].events).toHaveLength(501);
    expect(receive.mock.calls[0][0].status).toBe("succeeded");
  });

  it("limits work per tick without declaring completion while a backlog remains", async () => {
    vi.spyOn(api, "getRunStatus").mockResolvedValue({ run_id: "run_a", status: "succeeded" });
    vi.spyOn(api, "getRunMetrics").mockRejectedValue(new Error("offline"));
    vi.spyOn(api, "getRunEvents").mockImplementation(async (_id, after) => ({ run_id: "run_a",
      events: after < 2500 ? Array.from({ length: 500 }, (_, i) => event(after + i + 1)) : [event(2501)] }));
    const receive = vi.fn();
    const { result } = renderHook(() => useRunPolling("run_a", "waiting_human", receive));
    await act(async () => { await result.current(); });
    expect(receive.mock.calls[0][0].status).toBeUndefined();
    await act(async () => { await result.current(); });
    expect(receive.mock.calls[1][0].status).toBe("succeeded");
    expect(receive.mock.calls[1][0].events[0].seq).toBe(2501);
  });

  it("drains stopped-run events before ending automatic polling", async () => {
    vi.useFakeTimers();
    const statusRequest = vi.spyOn(api, "getRunStatus").mockResolvedValue({ run_id: "run_a", status: "stopped" });
    vi.spyOn(api, "getRunMetrics").mockRejectedValue(new Error("offline"));
    const events = vi.spyOn(api, "getRunEvents")
      .mockResolvedValueOnce({ run_id: "run_a", events: Array.from({ length: 500 }, (_, i) => event(i + 1)) })
      .mockResolvedValueOnce({ run_id: "run_a", events: [event(501)] });
    const received: RunEvent[] = [];
    const { result } = renderHook(() => {
      const [status, setStatus] = useState("stopping");
      useRunPolling("run_a", status, (snapshot) => {
        received.push(...snapshot.events);
        if (snapshot.status) setStatus(snapshot.status);
      });
      return status;
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(result.current).toBe("stopped");
    expect(received).toHaveLength(501);
    expect(events).toHaveBeenCalledTimes(2);
    expect(statusRequest).toHaveBeenCalledOnce();
  });

  it("serializes refreshes and discards a late response after switching runs", async () => {
    let resolveStatus!: (value: { run_id: string; status: "running" }) => void;
    const status = vi.spyOn(api, "getRunStatus").mockImplementation(() => new Promise((resolve) => { resolveStatus = resolve; }));
    const events = vi.spyOn(api, "getRunEvents").mockResolvedValue({ run_id: "run_a", events: [event(1)] });
    const receive = vi.fn();
    const { result, rerender } = renderHook(({ id }) => useRunPolling(id, "waiting_human", receive), { initialProps: { id: "run_a" } });
    const first = result.current();
    expect(result.current()).toBe(first);
    expect(status).toHaveBeenCalledOnce();
    const signal = status.mock.calls[0][1]!;
    rerender({ id: "run_b" });
    expect(signal.aborted).toBe(true);
    await act(async () => { resolveStatus({ run_id: "run_a", status: "running" }); await first; });
    expect(events).not.toHaveBeenCalled();
    expect(receive).not.toHaveBeenCalled();
  });

  it("does not overlap timer-driven polls when a request is slow", async () => {
    vi.useFakeTimers();
    const status = vi.spyOn(api, "getRunStatus").mockImplementation(() => new Promise(() => {}));
    const { unmount } = renderHook(() => useRunPolling("run_a", "running", vi.fn()));
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(status).toHaveBeenCalledOnce();
    unmount();
    expect(status.mock.calls[0][1]!.aborted).toBe(true);
  });

  it("aborts an unresponsive poll and retries on the next tick", async () => {
    vi.useFakeTimers();
    const status = vi.spyOn(api, "getRunStatus").mockImplementation((_id, signal) => new Promise((_resolve, reject) => {
      signal!.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    const { unmount } = renderHook(() => useRunPolling("run_a", "running", vi.fn()));
    await act(async () => { await vi.advanceTimersByTimeAsync(16_000); });
    expect(status).toHaveBeenCalledTimes(2);
    expect(status.mock.calls[0][1]!.aborted).toBe(true);
    unmount();
  });
});
