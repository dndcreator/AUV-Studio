import type { RunEvent, WorkflowDefinition, WorkflowNode } from "../types";

export type SceneRegion = { id: string; label: string; x: number; y: number; width: number; height: number };
export type ScenePlacement = { node_id: string; region_id?: string; x: number; y: number };
export type SceneAsset = { kind: "background" | "portrait"; node_id?: string; url: string };
export type ScenePrimitive = { id: string; shape: "rect" | "circle" | "line"; x: number; y: number; width: number; height: number; color: string; label: string };
export type SceneBinding = { kind: "location" | "color"; concept_id: string; target_id: string; values: Record<string, string> };
export type ScenePresentation = {
  version: 1;
  renderer: string;
  theme: Partial<Record<"background" | "surface" | "ink" | "accent", string>>;
  regions: SceneRegion[];
  placements: ScenePlacement[];
  assets: SceneAsset[];
  primitives?: ScenePrimitive[];
  bindings?: SceneBinding[];
};
export type SceneActor = {
  id: string; name: string; kind: string; profile: string; status: string;
  action: string; eventSeq: number; position: { x: number; y: number };
  control?: string;
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
  const primitiveIds = new Set<string>();
  const primitives = rows(raw.primitives).slice(0, 80).flatMap((r): ScenePrimitive[] => {
    const id = text(r.id, 100); const color = text(r.color);
    if (!id || primitiveIds.has(id) || !["rect", "circle", "line"].includes(String(r.shape)) || !/^#[0-9a-f]{6}$/i.test(color)) return [];
    primitiveIds.add(id);
    return [{ id, shape: r.shape as ScenePrimitive["shape"], x: coordinate(r.x, 0), y: coordinate(r.y, 0),
      width: Math.max(r.shape === "line" ? 0 : 1, coordinate(r.width, 20)), height: Math.max(r.shape === "line" ? 0 : 1, coordinate(r.height, 20)), color, label: text(r.label, 100) }];
  });
  const bindings = rows(raw.bindings).slice(0, 32).flatMap((r): SceneBinding[] => {
    const kind = r.kind; const target = text(r.target_id, 100); const concept = text(r.concept_id, 100);
    if (!concept || (kind !== "location" && kind !== "color") || !(kind === "location" ? nodeIds : primitiveIds).has(target)) return [];
    const values = Object.fromEntries(Object.entries(object(r.values)).slice(0, 16).filter(([, value]) =>
      typeof value === "string" && (kind === "location" ? seen.has(value) : /^#[0-9a-f]{6}$/i.test(value))));
    return [{ kind, concept_id: concept, target_id: target, values: values as Record<string, string> }];
  });
  return { version: 1, renderer: renderer === "spatial" && !regions.length ? "network" : renderer, theme, regions, placements, assets, primitives, bindings };
}

function evolvePresentation(presentation: ScenePresentation, events: RunEvent[]): ScenePresentation {
  const accepted = new Map(events.filter(e => e.event === "succeeded").map(e => [e.seq, e.node_id]));
  const outcomes = new Set(events.filter(e => e.event === "player_outcome").map(e => e.seq));
  const restored = events.filter(e => e.event === "state_restored" && /^[a-f0-9]{64}$/.test(String(e.payload.source_event_hash))
    && typeof e.payload.source_run_id === "string" && Number(e.payload.checkpoint_seq) > 0).sort((a, b) => Number(b.seq) - Number(a.seq))[0];
  const baseline = object(restored?.payload.dynamic_state);
  const updates = events.filter(e => (e.event === "dynamic_state_updated" || e === restored) && object(e.payload.coding).status !== "pending")
    .sort((a, b) => Number(b.seq) - Number(a.seq));
  if (!updates.length) return presentation;
  const dynamic = object(updates[0].payload.dynamic_state);
  const concepts = object(dynamic.concepts); const values = object(dynamic.values);
  const placements = presentation.placements.map(p => ({ ...p }));
  const primitives = (presentation.primitives ?? []).map(p => ({ ...p }));
  for (const binding of presentation.bindings ?? []) {
    const schema = object(concepts[binding.concept_id]); const current = object(values[binding.concept_id]);
    const refs = rows(current.source_refs);
    const inherited = restored && Number(restored.seq) <= Number(updates[0].seq)
      && JSON.stringify(object(baseline.values)[binding.concept_id]) === JSON.stringify(current)
      && ["visibility", "owner_id", "subject_id", "predicate", "value_type"].every(key => object(object(baseline.concepts)[binding.concept_id])[key] === schema[key]);
    if (schema.visibility !== "global" || schema.status !== "active" || !refs.length
        || !["action", "player_outcome"].includes(String(current.evidence_kind))
        || !(inherited || refs.every(ref => Number(ref.event_seq) < Number(updates[0].seq) && (
          current.evidence_kind === "action" && accepted.get(Number(ref.event_seq)) === ref.node_id
          || current.evidence_kind === "player_outcome" && ref.event_type === "player_outcome" && outcomes.has(Number(ref.event_seq))
        )))) continue;
    const mapped = binding.values[String(current.value)];
    if (!mapped) continue;
    if (binding.kind === "location" && schema.subject_id === binding.target_id) {
      const index = placements.findIndex(p => p.node_id === binding.target_id);
      const next = { node_id: binding.target_id, region_id: mapped, x: 24, y: 64 };
      if (index >= 0) placements[index] = next; else placements.push(next);
    } else if (binding.kind === "color") {
      const target = primitives.find(p => p.id === binding.target_id); if (target) target.color = mapped;
    }
  }
  return { ...presentation, placements, primitives };
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
  const presentation = evolvePresentation(readPresentation(workflow.environment.simulation?.presentation, ids), events);
  const latest = new Map<string, RunEvent>();
  const actions = new Map<string, { text: string; seq: number }>();
  const controls = new Map<string, string>(); const handoffs = new Set<string>(); const waiting = new Set<string>();
  for (const event of events) {
    const actor = String(event.payload.actor_id ?? event.node_id);
    if (event.event === "state_restored") {
      for (const [id, control] of Object.entries(object(event.payload.player_controls))) controls.set(id, String(control));
      for (const action of rows(event.payload.public_actions)) {
        if (ids.has(String(action.node_id)) && text(action.action, 2000)) actions.set(String(action.node_id), { text: text(action.action, 2000), seq: Number(action.event_seq) });
      }
    }
    if (event.event === "player_control_requested") handoffs.add(actor);
    if (event.event === "player_control_changed") { controls.set(actor, String(event.payload.control)); handoffs.delete(actor); }
    if (event.event === "player_control_rejected") handoffs.delete(actor);
    if (event.event === "player_waiting") { if (event.payload.waiting) waiting.add(actor); else waiting.delete(actor); }
    if (!ids.has(event.node_id)) continue;
    if (["queued", "running", "succeeded", "failed", "waiting_human", "skipped"].includes(event.event)
        && Number(event.seq ?? 0) >= Number(latest.get(event.node_id)?.seq ?? 0)) latest.set(event.node_id, event);
    if (publicActionText(event) && Number(event.seq ?? 0) >= Number(actions.get(event.node_id)?.seq ?? 0)) actions.set(event.node_id, { text: publicActionText(event), seq: Number(event.seq) });
  }
  const actors = entities.map((node, index) => {
    const action = actions.get(node.id);
    return { id: node.id, name: text(node.config.entity_name) || text(node.config.profile) || node.id,
      kind: text(node.config.entity_type) || node.type, profile: text(node.config.entity_profile),
      status: waiting.has(node.id) ? "waiting_player" : latest.get(node.id)?.event ?? "idle", action: action?.text ?? "",
      control: handoffs.has(node.id) ? "handoff" : controls.get(node.id) ?? "autonomous",
      eventSeq: Number(action?.seq ?? 0), position: node.position ?? { x: (index % 3) * 330, y: Math.floor(index / 3) * 250 } };
  });
  return { presentation, actors, connections: workflow.edges.filter((e) => ids.has(e.source) && ids.has(e.target))
    .map((e) => ({ id: e.id, source: e.source, target: e.target, label: e.interaction?.relation ?? "" })) };
}
