import type { UiMode } from "../appTypes";
import { useTranslation } from "react-i18next";
import { StudioIcon } from "./StudioIcon";

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
  const { t } = useTranslation();
  const modeLabel = t(`studio.mode.${props.mode}`);
  const statusLabel = t(`studio.status.${props.runStatus}`, { defaultValue: props.runStatus });
  return (
    <section className="studio-status-rail" aria-label={props.copy("工作室状态", "Studio status")}>
      <div className="status-rail-brand">
        <span className="status-rail-signal" aria-hidden="true" />
        <strong>AUV</strong>
        <small>SIMULATION STUDIO</small>
      </div>
      <button className={`status-rail-item ${props.modelReady ? "ready" : "attention"}`} onClick={props.onModel}>
        <span><i className="model-ready-dot" aria-hidden="true" />{props.copy("模型", "Model")}</span>
        <strong title={props.modelReady ? props.modelName : undefined}>{props.modelReady ? props.modelName : props.copy("配置", "Setup")}</strong>
      </button>
      <button className="status-rail-item" onClick={props.onMode}>
        <span><StudioIcon name="settings" />{props.copy("模式", "Mode")}</span>
        <strong>{modeLabel}</strong>
      </button>
      <div className="status-rail-item">
        <span><StudioIcon name="group" />{props.copy("实体", "Entities")}</span>
        <strong>{props.entityCount}</strong>
      </div>
      <button className="status-rail-item" onClick={props.onBackground}>
        <span><StudioIcon name="world" />{t("studio.worldBook")}</span>
        <strong>{props.backgroundCount}</strong>
      </button>
      <div className={`status-rail-item run-state run-state-${props.runStatus}`}>
        <span>{props.copy("运行", "Run")}</span>
        <strong>{statusLabel}</strong>
        <small>{t("studio.eventCount", { count: props.eventCount })}</small>
      </div>
    </section>
  );
}
