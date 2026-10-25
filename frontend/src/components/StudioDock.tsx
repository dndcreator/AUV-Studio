type StudioDockProps = {
  isStarting: boolean;
  activePanel: "create" | "inspect" | "advanced" | null;
  isRunning: boolean;
  isStopping: boolean;
  tr: (key: string) => string;
  onCreate: () => void;
  onInspect: () => void;
  onAdvanced: () => void;
  onRun: () => void;
  onStop: () => void;
};

import { StudioIcon } from "./StudioIcon";

export function StudioDock(props: StudioDockProps) {
  return (
    <div className="studio-dock">
      <button className="dock-pill primary" onClick={props.onCreate} aria-pressed={props.activePanel === "create"}>
        <StudioIcon name="add" />
        {props.tr("create")}
      </button>
      <button className="dock-pill" onClick={props.onInspect} aria-pressed={props.activePanel === "inspect"}>
        <StudioIcon name="inspect" />
        <span className="action-label-full">{props.tr("inspector")}</span>
        <span className="action-label-compact">{props.tr("studio.edit")}</span>
      </button>
      <button className="dock-pill" onClick={props.onAdvanced} aria-pressed={props.activePanel === "advanced"}>
        <StudioIcon name="settings" />
        {props.tr("advanced")}
      </button>
      {props.isRunning || props.isStopping ? (
        <button className="dock-pill stop" onClick={props.onStop} disabled={props.isStopping}>
          <StudioIcon name="stop" />
          {props.isStopping ? props.tr("stopping") : props.tr("stopRun")}
        </button>
      ) : (
        <button className="dock-pill run" onClick={props.onRun} disabled={props.isStarting}>
          <StudioIcon name="play" />
          {props.isStarting ? props.tr("starting") : props.tr("run")}
        </button>
      )}
    </div>
  );
}

