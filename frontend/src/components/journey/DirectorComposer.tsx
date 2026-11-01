import { useTranslation } from "react-i18next";
import { StudioIcon } from "../StudioIcon";

type Props = {
  value: string; busy: boolean; running: boolean; stopping: boolean; canStart: boolean; draft: boolean;
  needsRefresh: boolean; onChange: (text: string) => void; onSend: () => void; onStart: () => void;
  onStop: () => void; onResults: () => void; hasResults: boolean;
};
export function DirectorComposer(props: Props) {
  const { t } = useTranslation();
  return <form className="director-composer" onSubmit={e => { e.preventDefault(); if (!props.busy && props.value.trim()) props.onSend(); }}>
    <span className="composer-symbol" aria-hidden="true"><StudioIcon name="event" /></span>
    <textarea value={props.value} onChange={e => props.onChange(e.target.value)} rows={1} disabled={props.busy}
      aria-label={t("journey.directorMessage")} placeholder={t(props.running ? "journey.intervenePlaceholder" : "journey.adjustPlaceholder")}
      onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!props.busy && props.value.trim()) props.onSend(); } }} />
    {props.busy ? <span role="status" className="journey-spinner" aria-label={t("journey.preparing")} /> : null}
    <button className="composer-send" type="submit" disabled={props.busy || !props.value.trim()} aria-label={t("journey.send")}><StudioIcon name="arrow" /></button>
    {props.running || props.stopping ? <button className="btn warning" type="button" disabled={props.stopping} onClick={props.onStop}>
      <StudioIcon name="stop" />{t(props.stopping ? "stopping" : "stopRun")}</button>
      : <button className="btn primary" type="button" disabled={!props.canStart || props.busy} onClick={props.onStart}>
        <StudioIcon name="play" />{t(props.needsRefresh ? "journey.refreshPlan" : props.draft ? "journey.confirmStart" : "journey.start")}
      </button>}
    {props.hasResults ? <button type="button" className="btn" onClick={props.onResults}>{t("journey.results")}</button> : null}
  </form>;
}
