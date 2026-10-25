import { useTranslation } from "react-i18next";
import type { Lang } from "../appTypes";
import { useEffect, useRef } from "react";
import { StudioIcon } from "./StudioIcon";

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
  const menuRef = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) menuRef.current?.removeAttribute("open");
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && menuRef.current?.open) {
        menuRef.current.removeAttribute("open");
        menuRef.current.querySelector("summary")?.focus();
      }
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, []);

  return (
    <header className="topbar">
      <div className="brand-block">
        <div className="brand-mark" aria-hidden="true">
          <span className="brand-screen">AUV</span>
          <span className="brand-antenna" />
        </div>
        <div>
          <div className="brand-title">AUV</div>
          <div className="brand-subtitle">SIMULATION STUDIO</div>
        </div>
      </div>

      <div className="workflow-title-field">
        <StudioIcon name="edit" />
        <input
          className="workflow-name"
          value={props.workflowName}
          onChange={(e) => props.onWorkflowNameChange(e.target.value)}
          aria-label={t("workflowName")}
          placeholder={t("workflowName")}
        />
      </div>

      <div className="topbar-actions">
        <select className="language-select" value={props.lang} onChange={(e) => props.onLangChange(e.target.value as Lang)} aria-label={t("studio.language")}>
          <option value="zh-CN">中文</option>
          <option value="en-US">EN</option>
        </select>
        <button className="btn topbar-save" onClick={props.onSave} disabled={props.isSaving} aria-busy={props.isSaving} aria-label={props.isSaving ? t("savingProject") : t("saveProject")}>
          <StudioIcon name="save" />
          <span className="action-label-full">{props.isSaving ? t("savingProject") : t("saveProject")}</span>
          <span className="action-label-compact">{props.isSaving ? t("saving") : t("save")}</span>
        </button>
        <button className="btn" onClick={props.onOpenProject} disabled={props.isRunning || props.isStopping} aria-label={t("openProject")}>
          <StudioIcon name="folder" />
          <span className="action-label-full">{t("openProject")}</span>
          <span className="action-label-compact">{t("studio.open")}</span>
        </button>
        {props.isRunning || props.isStopping ? (
          <button className="btn warning" onClick={props.onStop} disabled={props.isStopping}>
            <StudioIcon name="stop" />
            {props.isStopping ? t("stopping") : t("stopRun")}
          </button>
        ) : (
          <button className="btn success" onClick={props.onRun} disabled={props.isStarting} aria-busy={props.isStarting}>
            <StudioIcon name="play" />
            {props.isStarting ? t("starting") : t("run")}
          </button>
        )}
        <details className="project-actions" ref={menuRef}>
          <summary className="btn project-actions-trigger" aria-label={t("studio.projectActions")} title={t("studio.projectActions")}><StudioIcon name="more" /></summary>
          <div className="project-menu-panel">
            <button className="btn topbar-transfer" onClick={() => { props.onExport(); menuRef.current?.removeAttribute("open"); }}>
              <StudioIcon name="download" />
              {t("export")}
            </button>
            <label className="btn import-btn topbar-transfer" title={t("import")}>
              <StudioIcon name="upload" />
              {t("import")}
              <input
                type="file"
                accept="application/json"
                aria-label={t("import")}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    void props.onImport(file);
                    menuRef.current?.removeAttribute("open");
                  }
                  e.currentTarget.value = "";
                }}
              />
            </label>
          </div>
        </details>
      </div>
    </header>
  );
}

