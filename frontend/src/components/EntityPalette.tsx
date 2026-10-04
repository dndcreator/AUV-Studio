import type { Lang } from "../appTypes";
import { entityDefinitions, type EntityType } from "../simulationEntities";

const entityCodes: Record<EntityType, string> = {
  individual: "01",
  group: "N+",
  organization: "ORG",
  environment: "WORLD",
  event: "EVENT",
  artifact: "ITEM",
  human_gate: "GATE"
};

type EntityPaletteProps = {
  lang: Lang;
  counts: Partial<Record<EntityType, number>>;
  onAdd: (type: EntityType) => void;
};

export function EntityPalette({ lang, counts, onAdd }: EntityPaletteProps) {
  return (
    <div className="entity-library">
      {entityDefinitions.map((entity) => (
        <button className={`node-card entity-card entity-card-${entity.type}`} key={entity.type} onClick={() => onAdd(entity.type)}>
          <span className="entity-card-code">{entityCodes[entity.type]}</span>
          <span className="node-card-title">{lang === "en-US" ? entity.titleEn : entity.titleZh}</span>
          <span className="entity-card-count">{counts[entity.type] ?? 0}</span>
          <span className="entity-card-add" aria-hidden="true">+</span>
        </button>
      ))}
    </div>
  );
}
