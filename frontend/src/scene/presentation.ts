import type { RunEvent, WorkflowDefinition, WorkflowNode } from "../types";

export type SceneRegion = { id: string; label: string; x: number; y: number; width: number; height: number };
export type ScenePlacement = { node_id: string; region_id?: string; x: number; y: number };
export type SceneAsset = { kind: "background" | "portrait"; node_id?: string; url: string };
export type ScenePresentation = {
  version: 1;
  renderer: string;
  theme: Partial<Record<"background" | "surface" | "ink" | "accent", string>>;
  regions: SceneRegion[];
  placements: ScenePlacement[];
  assets: SceneAsset[];
};
export type SceneActor = {
  id: string; name: string; kind: string; profile: string; status: string;
  action: string; eventSeq: number; position: { x: number; y: number };
};
export type SceneFrame = {
  presentation: ScenePresentation;
  actors: SceneActor[];
  connections: { id: string; source: string; target: string; label: string }[];
};

const object = (v: unknown): Record<string, unknown> => v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};
const text = (v: unknown, max = 300) => typeof v === "string" ? v.slice(0, max) : "";
const coordinate = (v: unknown, fallback: number) => typeof v === "number" && Number.isFinite(v) ? Math.max(0, Math.min(v, 10000)) : fallback;
const rows = (v: unknown) => Array.isArray(v) ? v.slice(0, 200).map(object) : [];
const assetPath = /^\/api\/media\/generations\/[a-zA-Z0-9_-]+\/assets\/[a-zA-Z0-9_-]+\/content$/;

// This is untrusted display metadata, never executable CSS/HTML or world-state authority.
export function readPresentation(value: unknown, nodeIds: Set<string>): ScenePresentation {
  const raw = object(value);
  const empty: ScenePresentation = { version: 1, renderer: "network", theme: {}, regions: [], placements: [], assets: [] };
  if (raw.version !== 1) return empty;
  const theme: ScenePresentation["theme"] = {};
  for (const key of ["background", "surface", "ink", "accent"] as const) {
    const color = object(raw.theme)[key];
    if (typeof color === "string" && /^#[0-9a-f]{6}$/i.test(color)) theme[key] = color;
  }
  const seen = new Set<string>();
  const regions = rows(raw.regions).flatMap((r) => {
    const id = text(r.id, 100);
    if (!id || seen.has(id)) return [];
    seen.add(id);
    return [{ id, label: text(r.label), x: coordinate(r.x, 0), y: coordinate(r.y, 0),
      width: Math.max(200, coordinate(r.width, 500)), height: Math.max(180, coordinate(r.height, 400)) }];
  });
  const placed = new Set<string>();
  const placements = rows(raw.placements).flatMap((r) => {
    const id = text(r.node_id, 100); const region = text(r.region_id, 100);
    if (!nodeIds.has(id) || placed.has(id) || (region && !seen.has(region))) return [];
    placed.add(id);
    return [{ node_id: id, region_id: region || undefined, x: coordinate(r.x, 24), y: coordinate(r.y, 54) }];
  });
  const assets = rows(raw.assets).flatMap((r): SceneAsset[] => {
    const url = text(r.url, 500); const id = text(r.node_id, 100);
    if (!assetPath.test(url)) return [];
    if (r.kind === "background") return [{ kind: "background", url }];
    return r.kind === "portrait" && nodeIds.has(id) ? [{ kind: "portrait", node_id: id, url }] : [];
  });
  const renderer = typeof raw.renderer === "string" && /^[a-z][a-z0-9_-]{0,47}$/.test(raw.renderer) ? raw.renderer : "network";
  return { version: 1, renderer: renderer === "spatial" && !regions.length ? "network" : renderer, theme, regions, placements, assets };
}

export function isSceneActor(node: WorkflowNode): boolean {
  return ["agent", "external_agent", "human_checkpoint"].includes(node.type) && !/^(summary|report|director)_/.test(node.id);
}

export function publicActionText(event: RunEvent): string {
  if (event.event !== "succeeded") return "";
  const output = object(event.payload.output);
  const value = output.action ?? output.content ?? output.text;
  if (typeof value !== "string") return "";
  if (/^[\s]*[{\[]|^```/.test(value)) {
    try { return text(object(JSON.parse(value)).action, 2000); } catch { return ""; }
  }
  return /"raw"\s*:|chat\.completion/.test(value) ? "" : value.slice(0, 2000);
}

export function buildSceneFrame(workflow: WorkflowDefinition, events: RunEvent[]): SceneFrame {
  const entities = workflow.nodes.filter(isSceneActor);
  const ids = new Set(entities.map((node) => node.id));
  const presentation = readPresentation(workflow.environment.simulation?.presentation, ids);
  const latest = new Map<string, RunEvent>();
  const actions = new Map<string, RunEvent>();
  for (const event of events) {
    if (!ids.has(event.node_id)) continue;
    if (["queued", "running", "succeeded", "failed", "waiting_human", "skipped"].includes(event.event)
        && Number(event.seq ?? 0) >= Number(latest.get(event.node_id)?.seq ?? 0)) latest.set(event.node_id, event);
    if (publicActionText(event) && Number(event.seq ?? 0) >= Number(actions.get(event.node_id)?.seq ?? 0)) actions.set(event.node_id, event);
  }
  const actors = entities.map((node, index) => {
    const action = actions.get(node.id);
    return { id: node.id, name: text(node.config.entity_name) || text(node.config.profile) || node.id,
      kind: text(node.config.entity_type) || node.type, profile: text(node.config.entity_profile),
      status: latest.get(node.id)?.event ?? "idle", action: action ? publicActionText(action) : "",
      eventSeq: Number(action?.seq ?? 0), position: node.position ?? { x: (index % 3) * 330, y: Math.floor(index / 3) * 250 } };
  });
  return { presentation, actors, connections: workflow.edges.filter((e) => ids.has(e.source) && ids.has(e.target))
    .map((e) => ({ id: e.id, source: e.source, target: e.target, label: e.interaction?.relation ?? "" })) };
}
