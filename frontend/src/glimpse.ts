import type { RunEvent } from "./types";

export type GlimpseRow = { seq: number; speaker: string; event: string; text: string; time: string; score: number };

function cleanText(value: unknown): string {
  let text = typeof value === "string" ? value.trim() : "";
  if (!text) return "";
  if (text.startsWith("{") && text.endsWith("}")) {
    try {
      const parsed = JSON.parse(text) as Record<string, unknown>;
      text = String(parsed.content ?? parsed.text ?? parsed.summary ?? parsed.global_guidance ?? "").trim();
    } catch {
      return "";
    }
  }
  text = text
    .replace(/^```(?:json|markdown|text)?\s*/i, "")
    .replace(/\s*```$/i, "")
    .replace(/^\[(?:DIALOGUE|REPORT|INSTRUCTION|FEEDBACK|HANDOFF)\]\s*[^:]*:\s*/i, "")
    .trim();
  if (!text || /"raw"\s*:|"choices"\s*:|chat\.completion/i.test(text)) return "";
  return text.length > 700 ? `${text.slice(0, 697).trimEnd()}...` : text;
}

function outputText(payload: Record<string, unknown>): string {
  const output = (payload.output ?? {}) as Record<string, unknown>;
  for (const candidate of [output.content, output.text, output.summary, output.recommendation]) {
    const text = cleanText(candidate);
    if (text) return text;
  }
  return "";
}

export function buildGlimpseRows(events: RunEvent[]): GlimpseRow[] {
  const rows: GlimpseRow[] = [];
  const push = (event: RunEvent, speaker: string, kind: string, value: unknown) => {
    const text = cleanText(value);
    if (!text) return;
    rows.push({
      seq: Number(event.seq ?? 0),
      speaker,
      event: kind,
      text,
      time: new Date(event.timestamp).toLocaleTimeString(),
      score: scoreGlimpseRow(kind, text)
    });
  };

  for (const event of events) {
    const payload = (event.payload ?? {}) as Record<string, unknown>;
    // Automatic quality corrections are backstage control signals. Exposing them
    // turns the scene feed into a prompt/debug console instead of a broadcast.
    if (event.event === "director_corrected") {
      continue;
    }
    if (event.event === "nodes_activated" || event.event === "episode_audited") {
      continue;
    }
    if (event.event === "director_overridden") {
      push(event, "Director", event.event, payload.guidance ?? payload.command_text);
      continue;
    }
    if (event.event === "director_vote_started") {
      push(event, "Director", event.event, payload.topic ?? "A critical vote has started.");
      continue;
    }
    if (event.event === "director_vote_finished") {
      const counts = (payload.counts ?? {}) as Record<string, unknown>;
      push(event, "Director", event.event, `${String(payload.topic ?? "Vote")}: ${payload.passed ? "passed" : "not passed"} (${String(counts.yes ?? 0)} / ${String(counts.no ?? 0)})`);
      continue;
    }
    if (event.event === "waiting_human") {
      const output = (payload.output ?? {}) as Record<string, unknown>;
      push(event, event.node_id, event.event, output.question ?? "Waiting for your decision.");
      continue;
    }
    if (event.event === "failed") {
      const error = (payload.error ?? {}) as Record<string, unknown>;
      push(event, event.node_id, event.event, error.message ?? payload.message ?? "Execution failed.");
      continue;
    }
    if (event.event !== "succeeded") continue;
    if (event.node_id.startsWith("director_") || event.node_id.startsWith("summary_") || event.node_id.startsWith("report_")) continue;
    push(event, event.node_id, "content", outputText(payload));
  }

  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = `${row.speaker}|${row.event}|${row.text}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function scoreGlimpseRow(event: string, text: string): number {
  let score = event === "content" ? 72 : 68;
  if (event === "director_overridden") score = 95;
  else if (event === "failed") score = 98;
  else if (event === "waiting_human") score = 94;
  else if (event.startsWith("director_vote_")) score = 92;
  const lower = text.toLowerCase();
  if (/风险|冲突|矛盾|分歧|危机|失败|转折|责备|否决|反对|blocker|risk|conflict|critical|fail|turning point/.test(lower)) score += 12;
  if (/结论|建议|决定|行动|发现|真相|recommend|decision|proposal|next step|finding/.test(lower)) score += 8;
  return Math.max(0, Math.min(100, score));
}

export function pickAcceptedGlimpse(freshRows: GlimpseRow[], elapsedMs: number): GlimpseRow | null {
  if (freshRows.length === 0) return null;
  const best = freshRows.reduce((top, row) => (row.score > top.score ? row : top), freshRows[0]);
  if (best.score >= 90) return best;
  if (best.score >= 70 && elapsedMs >= 5000) return best;
  if (best.score >= 60 && elapsedMs >= 10000) return best;
  return null;
}
