import type { ChangeEvent } from "react";
import type { GlimpseRow } from "../glimpse";
import type { EvidencePack, RunReport } from "../types";
import { MediaStudio } from "./MediaStudio";

type Copy = (zh: string, en: string) => string;

type EvidenceOverlayProps = {
  open: boolean;
  pending: boolean;
  evidence: EvidencePack | null;
  copy: Copy;
  onFile: (file: File) => void;
  onUse: () => void;
  onSkip: () => void;
};

export function EvidenceOverlay({ open, pending, evidence, copy, onFile, onUse, onSkip }: EvidenceOverlayProps) {
  if (!open) return null;
  const selectFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) onFile(file);
    event.currentTarget.value = "";
  };

  return (
    <div className="modal-backdrop evidence-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card evidence-card">
        <div className="modal-head">
          <div>
            <span className="result-kicker">RESEARCH EVIDENCE</span>
            <h2>{copy("添加研究证据", "Add Research Evidence")}</h2>
          </div>
        </div>
        <label className="evidence-dropzone">
          <strong>{pending ? copy("正在本地解析...", "Parsing locally...") : copy("选择证据包或文件", "Choose evidence package or file")}</strong>
          <span>ZIP · XLSX · DOCX · CSV · JSON · TXT · MD</span>
          <input type="file" accept=".zip,.xlsx,.docx,.csv,.json,.txt,.md" disabled={pending} onChange={selectFile} />
        </label>
        {evidence ? (
          <div className="evidence-review">
            <div className="draft-contract">
              <span>{copy("已解析", "Parsed")}: {evidence.summary.parsed_files}</span>
              <span>{copy("跳过", "Skipped")}: {evidence.summary.skipped_files}</span>
              <span>{copy("失败", "Failed")}: {evidence.summary.failed_files}</span>
            </div>
            <div className="evidence-file-list">
              {evidence.items.map((item) => (
                <div className="evidence-file ok" key={item.id}><strong>{item.name}</strong><span>{item.kind}</span></div>
              ))}
              {[...evidence.skipped, ...evidence.errors].map((item, index) => (
                <div className="evidence-file skipped" key={`${item.name}-${index}`}><strong>{item.name}</strong><span>{item.reason}</span></div>
              ))}
            </div>
          </div>
        ) : null}
        <div className="result-actions">
          <button className="btn primary" disabled={!evidence || evidence.items.length === 0 || pending} onClick={onUse}>
            {copy("确认使用", "Use Evidence")}
          </button>
          <button className="btn" onClick={onSkip}>{copy("暂不提供", "Continue Without Evidence")}</button>
        </div>
      </div>
    </div>
  );
}

type LiveTranscriptOverlayProps = {
  open: boolean;
  rows: GlimpseRow[];
  finalText: string;
  copy: Copy;
  onClose: () => void;
};

export function LiveTranscriptOverlay({ open, rows, finalText, copy, onClose }: LiveTranscriptOverlayProps) {
  if (!open) return null;
  return (
    <div className="modal-backdrop live-transcript-backdrop" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal-card live-transcript-card" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div><h2>{copy("全量实况", "Full Live Scene")}</h2><span>{copy(`${rows.length} 条实际互动`, `${rows.length} actual interactions`)}</span></div>
          <button className="btn" onClick={onClose}>{copy("关闭", "Close")}</button>
        </div>
        <div className="live-transcript-list">
          <div className="live-transcript-section-title">{copy("实际互动", "Actual Interactions")}</div>
          {rows.length === 0 ? <div className="live-window-empty">{copy("模拟开始后，对白和行动会出现在这里。", "Dialogue and actions will appear here once the simulation starts.")}</div> : null}
          {rows.map((row) => (
            <article className={`live-transcript-entry ${row.event}`} key={`transcript-${row.seq}-${row.speaker}-${row.event}`}>
              <header><strong>{row.speaker}</strong><time>{row.time}</time></header>
              <div>{row.text}</div>
            </article>
          ))}
          {finalText ? (
            <>
              <div className="live-transcript-section-title">{copy("最终演出", "Final Scene")}</div>
              <article className="live-transcript-entry final-scene">
                <header><strong>{copy("旁白", "Narrator")}</strong></header>
                <div>{finalText}</div>
              </article>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

type RunResultOverlayProps = {
  open: boolean;
  runId: string | null;
  finalText: string;
  report: RunReport | null;
  reportPending: boolean;
  copy: Copy;
  onClose: () => void;
  onOpenTranscript: () => void;
  onGenerateReport: (mode: "briefing" | "narrative") => void;
};

export function RunResultOverlay({
  open,
  runId,
  finalText,
  report,
  reportPending,
  copy,
  onClose,
  onOpenTranscript,
  onGenerateReport,
}: RunResultOverlayProps) {
  if (!open) return null;
  return (
    <div className="modal-backdrop result-backdrop" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal-card run-result-card" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div><span className="result-kicker">SIMULATION COMPLETE</span><h2>{copy("模拟结束", "Simulation Complete")}</h2></div>
          <button className="btn" onClick={onClose}>{copy("关闭", "Close")}</button>
        </div>
        <div className="run-result-output">
          <strong>{copy("本次结果", "Result")}</strong>
          <div>{finalText || copy("运行已经完成，暂无可展示的最终文本。", "The run completed without a final text output.")}</div>
        </div>
        <div className="result-actions">
          <button className="btn" onClick={onOpenTranscript}>{copy("查看完整过程", "View Full Scene")}</button>
          <button className="btn primary" disabled={!runId || reportPending} onClick={() => onGenerateReport("briefing")}>
            {reportPending ? copy("生成中...", "Generating...") : copy("生成正式汇报", "Generate Briefing")}
          </button>
          <button className="btn warning" disabled={!runId || reportPending} onClick={() => onGenerateReport("narrative")}>
            {copy("改写为文学作品", "Create Narrative")}
          </button>
        </div>
        {report ? (
          <div className="run-result-report">
            <div className="sidebar-header"><strong>{report.title}</strong><span className="count-chip">{report.mode}</span></div>
            <div>{report.report_markdown}</div>
          </div>
        ) : null}
        {runId ? <MediaStudio runId={runId} sourceText={report?.report_markdown ?? finalText} copy={copy} /> : null}
      </div>
    </div>
  );
}
