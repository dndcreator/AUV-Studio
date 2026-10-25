import { useCallback, useEffect, useRef } from "react";
import { api } from "../api";
import type { RunCheckpoint, RunEvent, RunMetrics } from "../types";

type Snapshot = { events: RunEvent[]; status?: string; metrics?: RunMetrics; checkpoints?: RunCheckpoint[] };
type Scope = { id: string; cursor: number; controller: AbortController; pending?: Promise<void>; lastStats: number; finished?: boolean };
const activeStates = new Set(["queued", "pending", "running", "stopping"]);

export function mergeRunEvents(previous: RunEvent[], incoming: RunEvent[]): RunEvent[] {
  const known = new Set(previous.map((event) => event.seq));
  const fresh: RunEvent[] = [];
  for (const event of incoming) {
    if (event.seq !== undefined && known.has(event.seq)) continue;
    known.add(event.seq);
    fresh.push(event);
  }
  return fresh.length ? [...previous, ...fresh].sort((a, b) => Number(a.seq) - Number(b.seq)) : previous;
}

export function useRunPolling(runId: string | null, status: string, onSnapshot: (snapshot: Snapshot) => void) {
  const scopeRef = useRef<Scope | null>(null);
  const onSnapshotRef = useRef(onSnapshot);
  onSnapshotRef.current = onSnapshot;

  useEffect(() => {
    if (!runId) return;
    const scope: Scope = { id: runId, cursor: 0, controller: new AbortController(), lastStats: 0 };
    scopeRef.current = scope;
    return () => {
      scope.controller.abort();
      if (scopeRef.current === scope) scopeRef.current = null;
    };
  }, [runId]);

  const refresh = useCallback((requestedId?: string): Promise<void> => {
    const scope = scopeRef.current;
    if (!scope || (requestedId && scope.id !== requestedId)) return Promise.resolve();
    if (scope.pending) return scope.pending;
    const attempt = new AbortController();
    let timeout: ReturnType<typeof setTimeout>;
    const abort = () => { clearTimeout(timeout); attempt.abort(); };
    scope.controller.signal.addEventListener("abort", abort, { once: true });
    timeout = setTimeout(abort, 15_000);
    const current = () => scopeRef.current === scope && !scope.controller.signal.aborted && !attempt.signal.aborted;
    scope.pending = (async () => {
      try {
        const signal = attempt.signal;
        const detail = await api.getRunStatus(scope.id, signal);
        const events: RunEvent[] = [];
        let cursor = scope.cursor;
        let caughtUp = false;
        for (let page = 0; page < 5 && current(); page += 1) {
          const batch = await api.getRunEvents(scope.id, cursor, signal);
          events.push(...batch.events);
          const next = batch.events.reduce((max, event) => Math.max(max, Number(event.seq ?? 0)), cursor);
          caughtUp = batch.events.length < 500;
          if (next <= cursor || caughtUp) { cursor = next; break; }
          cursor = next;
        }
        if (!current()) return;
        scope.cursor = cursor;
        scope.finished = caughtUp && !activeStates.has(detail.status);
        // Publish the final status only after the transcript has caught up.
        onSnapshotRef.current({ events, status: caughtUp ? detail.status : undefined });
        if (Date.now() - scope.lastStats >= 5000 || !activeStates.has(detail.status)) {
          scope.lastStats = Date.now();
          try {
            const metrics = await api.getRunMetrics(scope.id, signal);
            if (current()) onSnapshotRef.current({ events: [], metrics });
            const checkpoints = await api.getRunCheckpoints(scope.id, 80, signal);
            if (current()) onSnapshotRef.current({ events: [], checkpoints: checkpoints.checkpoints });
          } catch { /* Optional telemetry must not discard a valid event page. */ }
        }
      } catch { /* A transient polling failure is retried by the next tick. */ }
    })().finally(() => {
      clearTimeout(timeout);
      scope.controller.signal.removeEventListener("abort", abort);
      scope.pending = undefined;
    });
    return scope.pending;
  }, []);

  useEffect(() => {
    if (!runId || !activeStates.has(status)) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      await refresh(runId);
      if (!stopped && !scopeRef.current?.finished) timer = setTimeout(tick, 1000);
    };
    void tick();
    return () => { stopped = true; clearTimeout(timer); };
  }, [runId, status, refresh]);

  return refresh;
}
