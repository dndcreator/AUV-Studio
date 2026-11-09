import { useTranslation } from "react-i18next";
import type { InterventionHistoryItem } from "../../playerTypes";
import { ModalFrame } from "../PanelFrame";

type Props = { open: boolean; items: InterventionHistoryItem[]; busy: boolean; more: boolean; canOverride: boolean;
  onClose: () => void; onMore: () => void; onBranch: (seq: number) => void; onTrace: (seq: number) => void;
  onOverride: (item: InterventionHistoryItem) => void; names: Record<string, string> };

export function InterventionHistory(props: Props) {
  const { t } = useTranslation();
  if (!props.open) return null;
  return <ModalFrame title={t("intervention.history")} onClose={props.onClose} closeLabel={t("close")}
    className="intervention-history-card">
    {props.busy ? <span role="status">{t("loading")}</span> : null}
    {!props.items.length && !props.busy ? <p>{t("intervention.noHistory")}</p> : null}
    {props.items.map(item => <article className="intervention-history-row" key={item.request_seq}>
      <header><strong>{t(`intervention.${item.mode === "control" ? "control" : item.mode}`)}</strong>
        {item.actor_id ? <span>{props.names[item.actor_id] ?? item.actor_id}</span> : null}
        <span className={`intervention-state ${item.status}`}>{t(`intervention.status.${item.status}`)}</span>
        <time>{new Date(item.timestamp).toLocaleString()}</time></header>
      <p>{item.mode === "control" ? t(item.control === "player_controlled" ? "intervention.takeover" : "intervention.release") : item.instruction}</p>
      {item.code ? <p className="intervention-error">{t(`intervention.errors.${item.code}`, { defaultValue: t("intervention.failed") })}</p> : null}
      <div className="intervention-history-actions">
        <button className="btn" type="button" onClick={() => props.onTrace(item.event_seq)}>{t("intervention.trace")}</button>
        {item.before_checkpoint_seq ? <button className="btn" type="button" onClick={() => props.onBranch(item.before_checkpoint_seq!)}>{t("intervention.branchBefore")}</button> : null}
        {item.after_checkpoint_seq ? <button className="btn" type="button" onClick={() => props.onBranch(item.after_checkpoint_seq!)}>{t("intervention.branchAfter")}</button> : null}
        {props.canOverride && item.mode === "outcome" && item.status === "rejected" && item.code === "outcome_conflict"
          ? <button className="btn warning" type="button" onClick={() => props.onOverride(item)}>{t("intervention.override")}</button> : null}
      </div>
    </article>)}
    {props.more ? <button className="btn" disabled={props.busy} onClick={props.onMore}>{t("intervention.more")}</button> : null}
  </ModalFrame>;
}
