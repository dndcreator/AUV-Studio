import { useRef } from "react";
import { useTranslation } from "react-i18next";
import type { Lang } from "../../appTypes";
import { StudioIcon } from "../StudioIcon";

type Props = {
  home: boolean; title: string; lang: Lang; editor: boolean; hasDraft: boolean;
  onHome: () => void; onLanguage: (lang: Lang) => void; onProjects: () => void; onSettings: () => void;
  onEditor: () => void; onScene: () => void; onAdvanced: () => void; onManual: () => void;
  onTemplates: () => void; onSave: () => void; onExport: () => void; onImport: (file: File) => void;
  onTitle: (value: string) => void;
};
export function JourneyHeader(props: Props) {
  const { t } = useTranslation();
  const menu = useRef<HTMLDetailsElement>(null);
  const act = (action: () => void) => { menu.current?.removeAttribute("open"); action(); };
  return <header className="journey-header">
    <button className="journey-brand" onClick={props.onHome} aria-label={t("journey.home")}>AUV</button>
    {!props.home ? <><span className="journey-header-divider" /><input className="journey-project-title" aria-label={t("name")} value={props.title} onChange={e => props.onTitle(e.target.value)} />
      <nav className="journey-view-tabs" aria-label={t("journey.workspaceView")}>
        <button aria-pressed={!props.editor} onClick={props.onScene}>{t("journey.scene")}</button>
        <button aria-pressed={props.editor} disabled={props.hasDraft} onClick={props.onEditor}>{t("journey.editor")}</button>
      </nav></> : null}
    <div className="journey-header-actions">
      <button onClick={props.onProjects}><StudioIcon name="folder" />{t("journey.projects")}</button>
      <button onClick={props.onSettings}><StudioIcon name="settings" />{t("journey.settings")}</button>
      <select className="journey-language" aria-label={t("studio.language")} value={props.lang} onChange={e => props.onLanguage(e.target.value as Lang)}>
        <option value="zh-CN">中文</option><option value="en-US">EN</option>
      </select>
      <details ref={menu} className="journey-more" onKeyDown={e => { if (e.key === "Escape") menu.current?.removeAttribute("open"); }}>
        <summary aria-label={t("studio.projectActions")}><StudioIcon name="more" /></summary>
        <div className="journey-menu">
          <button onClick={() => act(props.onSave)} disabled={props.hasDraft}>{t("save")}</button>
          <button onClick={() => act(props.onExport)}>{t("export")}</button>
          <label>{t("import")}<input type="file" accept=".json" aria-label={t("import")} onChange={e => {
            const file = e.target.files?.[0]; if (file) props.onImport(file); e.currentTarget.value = ""; menu.current?.removeAttribute("open");
          }} /></label>
          <button onClick={() => act(props.onTemplates)}>{t("templates")}</button>
          <button onClick={() => act(props.onManual)}>{t("journey.manual")}</button>
          <button onClick={() => act(props.onAdvanced)}>{t("advanced")}</button>
        </div>
      </details>
    </div>
  </header>;
}
