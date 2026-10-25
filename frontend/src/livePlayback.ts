import type { RunEvent } from "./types";

export type LivePlaybackMode = "sync" | "paced";

export function broadcastDelay(events: RunEvent[], backlog: number): number {
  const times = events.filter((event) => event.event === "succeeded").slice(-9).map((event) => Date.parse(event.timestamp));
  const gaps = times.slice(1).map((time, index) => time - times[index]).filter((gap) => Number.isFinite(gap) && gap > 0).sort((a, b) => a - b);
  const cadence = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 4500;
  return Math.round(Math.max(1200, Math.min(10_000, cadence) / Math.sqrt(Math.max(1, backlog))));
}
