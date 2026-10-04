import type { UiMode } from "../appTypes";

type StudioStatusRailProps = {
  copy: (zh: string, en: string) => string;
  modelReady: boolean;
  modelName: string;
  mode: UiMode;
  entityCount: number;
  backgroundCount: number;
  runStatus: string;
  eventCount: number;
  onModel: () => void;
  onMode: () => void;
  onBackground: () => void;
};

export function StudioStatusRail(props: StudioStatusRailProps) {
  return (
    <section className="studio-status-rail" aria-label={props.copy("工作室状态", "Studio status")}>
      <div className="status-rail-brand">
        <span className="status-rail-signal" aria-hidden="true" />
        <strong>AUV</strong>
        <small>SIMULATION STUDIO</small>
      </div>
      <button className={`status-rail-item ${props.modelReady ? "ready" : "attention"}`} onClick={props.onModel}>
        <span>{props.copy("模型", "Model")}</span>
        <strong>{props.modelReady ? props.modelName : props.copy("配置", "Setup")}</strong>
      </button>
      <button className="status-rail-item" onClick={props.onMode}>
        <span>{props.copy("模式", "Mode")}</span>
        <strong>{props.mode}</strong>
      </button>
      <div className="status-rail-item">
        <span>{props.copy("实体", "Entities")}</span>
        <strong>{props.entityCount}</strong>
      </div>
      <button className="status-rail-item" onClick={props.onBackground}>
        <span>WORLD BOOK</span>
        <strong>{props.backgroundCount}</strong>
      </button>
      <div className={`status-rail-item run-state run-state-${props.runStatus}`}>
        <span>{props.copy("运行", "Run")}</span>
        <strong>{props.runStatus}</strong>
        <small>{props.eventCount}</small>
      </div>
    </section>
  );
}
