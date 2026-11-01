import { useEffect, useMemo, useRef, type CSSProperties, type ComponentType } from "react";
import { Background, Controls, Handle, Position, ReactFlow, type NodeProps, type ReactFlowInstance } from "reactflow";
import { useTranslation } from "react-i18next";
import { apiAssetUrl } from "../api";
import { StudioIcon } from "../components/StudioIcon";
import type { SceneActor, SceneFrame } from "./presentation";

export type SceneRendererProps = { frame: SceneFrame; selectedId: string | null; onSelect: (id: string) => void };

function ActorCard({ actor, selected, portrait, onClick }: { actor: SceneActor; selected?: boolean; portrait?: string; onClick?: () => void }) {
  const { t } = useTranslation();
  return <button type="button" className={`scene-actor ${selected ? "selected" : ""} status-${actor.status}`} onClick={onClick}>
    <div className="scene-actor-heading">
      <span className={`scene-avatar kind-${actor.kind}`}>
        {portrait ? <img src={apiAssetUrl(portrait)} alt="" /> : <StudioIcon name={actor.kind === "group" ? "group" : actor.kind === "environment" ? "world" : actor.kind === "artifact" ? "artifact" : "person"} />}
      </span>
      <div><strong>{actor.name}</strong><span>{t(`journey.entity.${actor.kind}`, { defaultValue: actor.kind })}</span></div>
    </div>
    <p>{actor.action || actor.profile}</p>
    <span className="scene-actor-status"><i />{t(`studio.status.${actor.status}`, { defaultValue: actor.status })}</span>
  </button>;
}

type ActorData = { actor: SceneActor; selected: boolean; portrait?: string; onSelect: (id: string) => void };
function ActorNode({ data }: NodeProps<ActorData>) {
  return <><Handle type="target" position={Position.Top} isConnectable={false} />
    <ActorCard actor={data.actor} selected={data.selected} portrait={data.portrait} onClick={() => data.onSelect(data.actor.id)} />
    <Handle type="source" position={Position.Bottom} isConnectable={false} /></>;
}
const nodeTypes = { actor: ActorNode };

function NetworkRenderer({ frame, selectedId, onSelect }: SceneRendererProps) {
  const host = useRef<HTMLDivElement>(null);
  const flow = useRef<ReactFlowInstance | null>(null);
  useEffect(() => {
    if (!host.current || typeof ResizeObserver === "undefined") return;
    let scheduled = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(scheduled);
      scheduled = requestAnimationFrame(() => { void flow.current?.fitView({ padding: 0.35, maxZoom: 1 }); });
    });
    observer.observe(host.current);
    return () => { observer.disconnect(); cancelAnimationFrame(scheduled); };
  }, []);
  const nodes = useMemo(() => frame.actors.map((actor) => ({ id: actor.id, type: "actor", position: actor.position,
    data: { actor, selected: actor.id === selectedId, onSelect, portrait: frame.presentation.assets.find(a => a.kind === "portrait" && a.node_id === actor.id)?.url } })), [frame, selectedId, onSelect]);
  const edges = useMemo(() => frame.connections.map((edge) => ({ ...edge, type: "smoothstep" })), [frame.connections]);
  return <div ref={host} className="scene-network"><ReactFlow key={frame.actors.map(a => a.id).join("|")} nodes={nodes} edges={edges} nodeTypes={nodeTypes}
    onInit={instance => { flow.current = instance; }}
    onNodeClick={(_, node) => onSelect(node.id)} nodesDraggable={false} nodesConnectable={false}
    zoomOnScroll={false} panOnScroll={false} preventScrolling={false} fitView minZoom={0.15}
    fitViewOptions={{ padding: 0.35, maxZoom: 1 }} onlyRenderVisibleElements>
    <Background gap={32} size={1} /><Controls showInteractive={false} />
  </ReactFlow></div>;
}

function SpatialRenderer({ frame, selectedId, onSelect }: SceneRendererProps) {
  const { regions, placements, assets } = frame.presentation;
  const width = Math.max(900, ...regions.map(r => r.x + r.width + 30));
  const height = Math.max(600, ...regions.map(r => r.y + r.height + 30));
  const unplaced = frame.actors.filter(actor => !placements.some(p => p.node_id === actor.id));
  return <div className="spatial-scene-scroll"><div className="spatial-scene" style={{ width, height: height + Math.ceil(unplaced.length / 3) * 250 }}>
    {regions.map(region => <section className="scene-region" key={region.id} aria-label={region.label}
      style={{ left: region.x, top: region.y, width: region.width, height: region.height }}><strong>{region.label}</strong></section>)}
    {frame.actors.map((actor) => {
      const placement = placements.find(p => p.node_id === actor.id);
      const region = regions.find(r => r.id === placement?.region_id);
      const fallbackIndex = unplaced.findIndex(a => a.id === actor.id);
      const x = placement ? placement.x + (region?.x ?? 0) : 40 + (fallbackIndex % 3) * 285;
      const y = placement ? placement.y + (region?.y ?? 0) : height + 30 + Math.floor(fallbackIndex / 3) * 240;
      return <div className="spatial-actor" key={actor.id} style={{ left: x, top: y }}>
        <ActorCard actor={actor} selected={actor.id === selectedId} onClick={() => onSelect(actor.id)}
          portrait={assets.find(a => a.kind === "portrait" && a.node_id === actor.id)?.url} />
      </div>;
    })}
  </div></div>;
}

// Additional renderers consume the same read-only frame; they cannot mutate simulation state.
const renderers = new Map<string, ComponentType<SceneRendererProps>>([["network", NetworkRenderer], ["spatial", SpatialRenderer]]);
export function registerSceneRenderer(id: string, renderer: ComponentType<SceneRendererProps>) {
  if (!id || renderers.has(id)) throw new Error(`Scene renderer already registered: ${id}`);
  renderers.set(id, renderer);
  return () => renderers.delete(id);
}

export function SceneViewport(props: SceneRendererProps) {
  const { t } = useTranslation();
  const Renderer = renderers.get(props.frame.presentation.renderer) ?? NetworkRenderer;
  const theme = Object.fromEntries(Object.entries(props.frame.presentation.theme).map(([key, value]) => [`--scene-${key}`, value]));
  const background = props.frame.presentation.assets.find(a => a.kind === "background");
  return <div className="scene-viewport" style={{ ...theme, ...(background ? { backgroundImage: `url("${apiAssetUrl(background.url)}")` } : {}) } as CSSProperties}>
    {props.frame.actors.length ? <Renderer {...props} /> : <div className="scene-empty">{t("journey.emptyScene")}</div>}
  </div>;
}
