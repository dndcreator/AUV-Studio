import { useTranslation } from "react-i18next";
import type { Lang } from "../appTypes";

type TopbarProps = {
  lang: Lang;
  workflowName: string;
  isSaving: boolean;
  isStarting: boolean;
  isRunning: boolean;
  isStopping: boolean;
  onLangChange: (lang: Lang) => void;
  onWorkflowNameChange: (name: string) => void;
  onSave: () => void;
  onOpenProject: () => void;
  onRun: () => void;
  onStop: () => void;
  onExport: () => void;
  onImport: (file: File) => void | Promise<void>;
};

export function Topbar(props: TopbarProps) {
  const { t } = useTranslation();

  return (
    <header className="topbar">
      <div className="brand-block">
        <div className="brand-mark" aria-hidden="true">
          <span className="brand-screen">AUV</span>
          <span className="brand-antenna" />
        </div>
        <div>
          <div className="brand-title">AUV-TV</div>
        </div>
      </div>

      <input
        className="workflow-name"
        value={props.workflowName}
        onChange={(e) => props.onWorkflowNameChange(e.target.value)}
        aria-label="workflow-name"
        placeholder={t("workflowName")}
      />

      <div className="topbar-actions">
        <select className="language-select" value={props.lang} onChange={(e) => props.onLangChange(e.target.value as Lang)} aria-label="language">
          <option value="zh-CN">中文</option>
          <option value="en-US">EN</option>
        </select>
        <button className="btn primary" onClick={props.onSave} disabled={props.isSaving}>
          {props.isSaving ? t("savingProject") : t("saveProject")}
        </button>
        <button className="btn" onClick={props.onOpenProject} disabled={props.isRunning || props.isStopping}>
          {t("openProject")}
        </button>
        {props.isRunning || props.isStopping ? (
          <button className="btn warning" onClick={props.onStop} disabled={props.isStopping}>
            {props.isStopping ? t("stopping") : t("stopRun")}
          </button>
        ) : (
          <button className="btn success" onClick={props.onRun} disabled={props.isStarting}>
            {props.isStarting ? t("starting") : t("run")}
          </button>
        )}
        <button className="btn" onClick={props.onExport}>
          {t("export")}
        </button>
        <label className="btn import-btn">
          {t("import")}
          <input
            type="file"
            accept="application/json"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) {
                void props.onImport(file);
              }
              e.currentTarget.value = "";
            }}
          />
        </label>
      </div>
    </header>
  );
}

