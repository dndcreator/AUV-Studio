import type { Node } from "reactflow";
import type { RunEvent } from "./types";

const nodeStatuses = new Set(["queued", "running", "succeeded", "failed", "waiting_human", "skipped", "cancelled"]);

export function presentCanvasNodes(nodes: Node[], events: RunEvent[]): Node[] {
  const latest = new Map<string, RunEvent>();
  for (const event of events) {
    if (!nodeStatuses.has(event.event)) continue;
    const previous = latest.get(event.node_id);
    if (!previous || Number(event.seq ?? 0) >= Number(previous.seq ?? 0)) latest.set(event.node_id, event);
  }
  return nodes.map((node) => {
    const status = latest.get(node.id)?.event ?? "idle";
    return { ...node, data: { ...node.data, displayStatus: status }, className: `${node.className ?? ""} canvas-node-${status}`.trim() };
  });
}
