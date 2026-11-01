import { useTranslation } from "react-i18next";
import type { WorkflowSummary } from "../../types";
import { StudioIcon } from "../StudioIcon";

type Props = {
  value: string; busy: boolean; projects: WorkflowSummary[]; loading: boolean; canResume: boolean;
  onChange: (text: string) => void; onGenerate: () => void; onOpen: (id: string) => void;
  onResume: () => void; onManual: () => void; onUpload: (file: File) => void;
};
export function Launchpad(props: Props) {
  const { t, i18n } = useTranslation();
  return <main className="launchpad">
    <section className="launchpad-hero">
      <span className="launchpad-kicker">AUV / SIMULATION STUDIO</span>
      <h1>{t("journey.whatToSimulate")}</h1>
      <form className="launchpad-prompt" onSubmit={e => { e.preventDefault(); if (!props.busy && props.value.trim()) props.onGenerate(); }}>
        <textarea value={props.value} onChange={e => props.onChange(e.target.value)} disabled={props.busy}
          aria-label={t("journey.prompt")} placeholder={t("journey.promptPlaceholder")} rows={4} />
        <div className="launchpad-prompt-actions">
          <label className="journey-upload" title={t("journey.uploadPlan")}><StudioIcon name="upload" />
            <input type="file" accept=".txt,.md" aria-label={t("journey.uploadPlan")} disabled={props.busy} onChange={e => {
              const file = e.target.files?.[0]; if (file) props.onUpload(file); e.currentTarget.value = "";
            }} />
          </label>
          <button className="btn primary" type="submit" disabled={props.busy || !props.value.trim()}>
            {props.busy ? <><span className="journey-spinner" />{t("journey.preparing")}</> : <>{t("journey.generate")}<StudioIcon name="arrow" /></>}
          </button>
        </div>
      </form>
      <div className="launchpad-examples">{["business", "world", "characters"].map(key =>
        <button key={key} disabled={props.busy} onClick={() => props.onChange(t(`journey.examples.${key}.prompt`))}>{t(`journey.examples.${key}.label`)}</button>)}</div>
      <div className="launchpad-shortcuts">
        {props.canResume ? <button onClick={props.onResume}>{t("journey.resume")}</button> : null}
        <button onClick={props.onManual}>{t("journey.manual")}</button>
      </div>
    </section>
    <section className="launchpad-recents" aria-label={t("journey.recentProjects")}>
      <h2>{t("journey.recentProjects")}</h2>
      {props.loading ? <div className="journey-quiet">{t("journey.loading")}</div> : props.projects.length ? <div className="launchpad-project-grid">
        {props.projects.slice(0, 4).map(project => <button className="launchpad-project" key={project.id} onClick={() => props.onOpen(project.id)}>
          <span className="project-glyph"><StudioIcon name="folder" /></span><span><strong>{project.name}</strong>
            <small>{project.updated_at ? new Date(project.updated_at).toLocaleString(i18n.language) : `v${project.version}`}</small></span><StudioIcon name="arrow" />
        </button>)}
      </div> : <div className="journey-quiet">{t("journey.noProjects")}</div>}
    </section>
  </main>;
}
