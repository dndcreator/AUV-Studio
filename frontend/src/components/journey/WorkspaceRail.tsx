import { useTranslation } from "react-i18next";
import type { GlimpseRow } from "../../glimpse";
import type { WorkflowNode } from "../../types";
import type { SceneActor } from "../../scene/presentation";

type Props = {
  draft: boolean; mode: string; submode: string; goal: string; actors: SceneActor[];
  selected: WorkflowNode | null; rows: GlimpseRow[]; running: boolean; status: string;
  onMode: (mode: "roleplay" | "research" | "custom") => void;
  onSubmode: (mode: "simulation" | "research" | "consulting") => void;
  onSelect: (id: string | null) => void; onConfig: (id: string, field: string, value: string) => void;
  onAdvanced: () => void; onTranscript: () => void; onDiscard: () => void;
  onApplyEdit: () => void;
  humanQuestion: string; humanResponse: string; humanBusy: boolean;
  onHumanChange: (text: string) => void; onHumanConfirm: () => void;
};
export function WorkspaceRail(props: Props) {
  const { t } = useTranslation();
  return <aside className="workspace-rail">
    <header><h2>{t(props.selected ? "journey.participant" : props.draft ? "journey.draft" : props.running ? "journey.eventTrail" : "journey.scene")}</h2>
      {props.selected ? <button onClick={() => props.onSelect(null)} aria-label={t("journey.closeDetails")}>×</button> : <span className="journey-status">{t(`studio.status.${props.status}`, { defaultValue: props.status })}</span>}
    </header>
    <div className="workspace-rail-body">
      {props.status === "waiting_human" ? <section className="journey-human-decision">
        <h3>{t("journey.yourDecision")}</h3><p>{props.humanQuestion}</p>
        <textarea aria-label={t("journey.yourDecision")} rows={3} value={props.humanResponse} onChange={e => props.onHumanChange(e.target.value)} />
        <button className="btn primary" disabled={props.humanBusy || !props.humanResponse.trim()} onClick={props.onHumanConfirm}>{t("journey.confirmContinue")}</button>
      </section> : null}
      {props.selected ? <section className="journey-participant-fields">
        <label>{t("name")}<input value={String(props.selected.config.entity_name ?? props.selected.id)} disabled={props.running || !props.draft}
          onChange={e => props.onConfig(props.selected!.id, "entity_name", e.target.value)} /></label>
        <label>{t("journey.role")}<textarea rows={5} value={String(props.selected.config.entity_profile ?? props.selected.config.profile ?? "")} disabled={props.running || !props.draft}
          onChange={e => props.onConfig(props.selected!.id, "entity_profile", e.target.value)} /></label>
        <label>{t("journey.objective")}<textarea rows={3} value={String(props.selected.config.objective ?? (props.selected.config.identity_contract as Record<string, unknown> | undefined)?.objective ?? "")} disabled={props.running || !props.draft}
          onChange={e => props.onConfig(props.selected!.id, "objective", e.target.value)} /></label>
        <button className="btn" disabled={props.draft} onClick={props.onAdvanced}>{t("journey.moreConfiguration")}</button>
      </section> : <>
        {props.draft ? <section className="journey-plan-summary">
          <label>{t("journey.mode")}<select value={props.mode} onChange={e => props.onMode(e.target.value as "roleplay" | "research" | "custom")}>
            {["roleplay", "research", "custom"].map(mode => <option key={mode} value={mode}>{t(`studio.mode.${mode}`)}</option>)}
          </select></label>
          {props.mode === "research" ? <label>{t("journey.method")}<select value={props.submode} onChange={e => props.onSubmode(e.target.value as "simulation" | "research" | "consulting")}>
            {["simulation", "research", "consulting"].map(mode => <option key={mode} value={mode}>{t(`journey.research.${mode}`)}</option>)}
          </select></label> : null}
          <h3>{t("journey.objective")}</h3><p>{props.goal}</p>
        </section> : null}
        {!props.running && !props.rows.length ? <section className="journey-cast-list"><h3>{t("journey.participants", { count: props.actors.length })}</h3>
          {props.actors.map(actor => <button key={actor.id} onClick={() => props.onSelect(actor.id)}><span>{actor.name}</span><small>{t(`journey.entity.${actor.kind}`, { defaultValue: actor.kind })}</small></button>)}
        </section> : null}
        {props.rows.length ? <section className="journey-event-list">
          {[...props.rows].reverse().slice(0, 60).map(row => <button key={`${row.seq}-${row.event}-${row.speaker}`} onClick={props.onTranscript}>
            <div><strong>{row.speaker}</strong><time>{row.time}</time></div><p>{row.text}</p>
          </button>)}
        </section> : props.running ? <div className="journey-quiet" role="status"><span className="journey-spinner" />{t("journey.awaitingAction")}</div> : null}
      </>}
    </div>
    <footer>{props.draft ? <><button onClick={props.onApplyEdit}>{t("journey.applyEdit")}</button><button onClick={props.onDiscard}>{t("journey.discard")}</button></>
      : <button onClick={props.onTranscript}>{t("journey.fullScene")}</button>}</footer>
  </aside>;
}
