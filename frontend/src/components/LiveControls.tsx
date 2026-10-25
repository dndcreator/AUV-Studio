import { useTranslation } from "react-i18next";
import type { LivePlaybackMode } from "../livePlayback";

type Props = { mode: LivePlaybackMode; status: string; onChange: (mode: LivePlaybackMode) => void; onOpen: () => void };

export function LiveControls({ mode, status, onChange, onOpen }: Props) {
  const { t } = useTranslation();
  return <div className="live-window-head">
    <button type="button" className="live-open" onClick={(event) => { event.stopPropagation(); onOpen(); }}>{t("liveBroadcast")}</button>
    <div className="live-playback-actions">
      <button type="button" className="live-playback-toggle" aria-pressed={mode === "paced"}
        onClick={(event) => { event.stopPropagation(); onChange(mode === "sync" ? "paced" : "sync"); }}>
        {t(mode === "sync" ? "liveSync" : "livePaced")}
      </button>
      <strong>{t(`studio.status.${status}`, { defaultValue: status })}</strong>
    </div>
  </div>;
}
