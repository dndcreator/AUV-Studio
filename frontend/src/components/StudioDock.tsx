type StudioDockProps = {
  isStarting: boolean;
  isRunning: boolean;
  isStopping: boolean;
  tr: (key: string) => string;
  onCreate: () => void;
  onInspect: () => void;
  onAdvanced: () => void;
  onRun: () => void;
  onStop: () => void;
};

export function StudioDock(props: StudioDockProps) {
  return (
    <div className="studio-dock">
      <button className="dock-pill primary" onClick={props.onCreate}>
        {props.tr("create")}
      </button>
      <button className="dock-pill" onClick={props.onInspect}>
        {props.tr("inspector")}
      </button>
      <button className="dock-pill" onClick={props.onAdvanced}>
        {props.tr("advanced")}
      </button>
      {props.isRunning || props.isStopping ? (
        <button className="dock-pill stop" onClick={props.onStop} disabled={props.isStopping}>
          {props.isStopping ? props.tr("stopping") : props.tr("stopRun")}
        </button>
      ) : (
        <button className="dock-pill run" onClick={props.onRun} disabled={props.isStarting}>
          {props.isStarting ? props.tr("starting") : props.tr("run")}
        </button>
      )}
    </div>
  );
}

