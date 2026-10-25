import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import { useTranslation } from "react-i18next";
import { isEntityType, type EntityType } from "../simulationEntities";
import { StudioIcon, type StudioIconName } from "./StudioIcon";

const symbols: Record<EntityType, StudioIconName> = {
  individual: "person", group: "group", organization: "organization", environment: "world",
  event: "event", artifact: "artifact", human_gate: "gate",
};

type NodeData = {
  label?: string;
  nodeType?: string;
  config?: Record<string, unknown>;
  displayStatus?: string;
};

export const SimulationNode = memo(function SimulationNode({ data, isConnectable }: NodeProps<NodeData>) {
  const { t } = useTranslation();
  const entity = data.config?.entity_type;
  const status = data.displayStatus ?? "idle";
  return (
    <>
      <Handle type="target" position={Position.Top} isConnectable={isConnectable} />
      <div className={`simulation-node-content ${isEntityType(entity) ? "has-entity-symbol" : ""}`}>
        {isEntityType(entity) ? <span className="simulation-node-symbol"><StudioIcon name={symbols[entity]} /></span> : null}
        <strong className="simulation-node-name" title={data.label}>{data.label ?? data.nodeType}</strong>
        <span className={`simulation-node-state state-${status}`}>
          <span aria-hidden="true" />{t(`studio.status.${status}`, { defaultValue: status })}
        </span>
      </div>
      <Handle type="source" position={Position.Bottom} isConnectable={isConnectable} />
    </>
  );
});

export const studioNodeTypes = { default: SimulationNode };
