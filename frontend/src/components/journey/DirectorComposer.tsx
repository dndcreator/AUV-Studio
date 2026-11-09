import { useTranslation } from "react-i18next";
import { StudioIcon } from "../StudioIcon";
import type { ActorControl, InputMode } from "../../playerTypes";

type Props = {
  value: string; busy: boolean; running: boolean; stopping: boolean; canStart: boolean; draft: boolean;
  needsRefresh: boolean; onChange: (text: string) => void; onSend: () => void; onStart: () => void;
  onStop: () => void; onResults: () => void; hasResults: boolean;
  mode?: InputMode; onMode?: (mode: InputMode) => void; interventionsAvailable?: boolean; blocked?: boolean;
  actors?: { id: string; name: string }[]; actorId?: string; onActor?: (id: string) => void;
  control?: ActorControl | "handoff"; waiting?: boolean; onControl?: (control: ActorControl) => void;
  onHistory?: () => void;
};
export function DirectorComposer(props: Props) {
  const { t } = useTranslation();
  return <form className="director-composer" onSubmit={e => { e.preventDefault(); if (!props.busy && !props.blocked && props.value.trim()) props.onSend(); }}>
    <span className="composer-symbol" aria-hidden="true"><StudioIcon name="event" /></span>
    {props.interventionsAvailable ? <select className="composer-mode" aria-label={t("intervention.mode")}
      value={props.mode ?? "director"} disabled={props.busy} onChange={e => props.onMode?.(e.target.value as InputMode)}>
      <option value="director">{t("intervention.director")}</option>
      <option value="action">{t("intervention.action")}</option>
      <option value="outcome">{t("intervention.outcome")}</option>
    </select> : null}
    {props.interventionsAvailable && (props.mode === "action" || props.actorId) ? <div className="composer-actor-control">
      <select aria-label={t("intervention.actor")} value={props.actorId ?? ""} disabled={props.busy}
        onChange={e => props.onActor?.(e.target.value)}><option value="">{t("intervention.chooseActor")}</option>
        {props.actors?.map(actor => <option key={actor.id} value={actor.id}>{actor.name}</option>)}
      </select>
      {props.actorId ? <button type="button" className="btn" disabled={props.busy || props.control === "handoff"}
        onClick={() => props.onControl?.(props.control === "player_controlled" ? "autonomous" : "player_controlled")}>
        {t(props.control === "handoff" ? "intervention.handoff" : props.control === "player_controlled" ? "intervention.release" : "intervention.takeover")}</button> : null}
      {props.waiting ? <span role="status">{t("intervention.waiting")}</span> : null}
    </div> : null}
    <textarea value={props.value} onChange={e => props.onChange(e.target.value)} rows={1} disabled={props.busy || props.blocked}
      aria-label={t(props.mode === "action" ? "intervention.action" : props.mode === "outcome" ? "intervention.outcome" : "journey.directorMessage")}
      placeholder={t(props.mode === "action" ? "intervention.actionPlaceholder" : props.mode === "outcome" ? "intervention.placeholder" : props.running ? "journey.intervenePlaceholder" : "journey.adjustPlaceholder")}
      onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!props.busy && !props.blocked && props.value.trim()) props.onSend(); } }} />
    {props.busy ? <span role="status" className="journey-spinner" aria-label={t("journey.preparing")} /> : null}
    <button className="composer-send" type="submit" disabled={props.busy || props.blocked || !props.value.trim() || (props.mode === "action" && !props.actorId)} aria-label={t("journey.send")}><StudioIcon name="arrow" /></button>
    {props.onHistory ? <button type="button" className="btn" onClick={props.onHistory}>{t("intervention.history")}</button> : null}
    {props.running || props.stopping ? <button className="btn warning" type="button" disabled={props.stopping} onClick={props.onStop}>
      <StudioIcon name="stop" />{t(props.stopping ? "stopping" : "stopRun")}</button>
      : <button className="btn primary" type="button" disabled={!props.canStart || props.busy} onClick={props.onStart}>
        <StudioIcon name="play" />{t(props.needsRefresh ? "journey.refreshPlan" : props.draft ? "journey.confirmStart" : "journey.start")}
      </button>}
    {props.hasResults ? <button type="button" className="btn" onClick={props.onResults}>{t("journey.results")}</button> : null}
  </form>;
}
