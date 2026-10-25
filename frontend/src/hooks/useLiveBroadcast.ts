import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Node } from "reactflow";
import { buildGlimpseRows, type GlimpseRow } from "../glimpse";
import { toBroadcastLine } from "../appSupport";
import type { Lang, UiMode } from "../appTypes";
import type { RunEvent } from "../types";
import { broadcastDelay, type LivePlaybackMode } from "../livePlayback";

type BroadcastTone = "news" | "warroom" | "calm";

export function useLiveBroadcast(
  events: RunEvent[],
  nodes: Node[],
  lang: Lang,
  mode: UiMode,
  tone: BroadcastTone,
  playback: LivePlaybackMode = "sync",
) {
  const [snapshot, setSnapshot] = useState<GlimpseRow[]>([]);
  const cursorRef = useRef(0);
  const rowsRef = useRef<GlimpseRow[]>([]);
  const eventsRef = useRef(events);
  eventsRef.current = events;

  const rows = useMemo(() => {
    const labels = new Map(
      nodes.map((node) => {
        const data = (node.data as Record<string, unknown> | undefined) ?? {};
        const config = (data.config as Record<string, unknown> | undefined) ?? {};
        const narrator = lang === "en-US" ? "Narrator" : "旁白";
        const fallback = node.id.startsWith("summary_") ? narrator : String(data.label ?? node.id);
        return [node.id, String(config.entity_name || fallback)] as const;
      }),
    );
    return buildGlimpseRows(events).map((row) => ({ ...row, speaker: labels.get(row.speaker) ?? row.speaker }));
  }, [events, nodes, lang]);

  const current = useMemo(() => snapshot[snapshot.length - 1] ?? null, [snapshot]);
  const broadcastRows = useMemo(
    () => snapshot
      .slice(-16)
      .map((row) => ({ ...row, line: toBroadcastLine(row, mode, tone) }))
      .filter((row) => row.line.trim().length > 0)
      .reverse(),
    [snapshot, mode, tone],
  );

  const reset = useCallback(() => {
    setSnapshot([]);
    cursorRef.current = 0;
    rowsRef.current = [];
  }, []);

  useEffect(() => {
    if (rows.length < cursorRef.current) {
      cursorRef.current = 0;
      setSnapshot([]);
    }
    rowsRef.current = rows;
    if (playback === "sync") {
      cursorRef.current = rows.length;
      setSnapshot(rows.slice(-30));
    }
  }, [rows, playback]);

  useEffect(() => {
    if (playback !== "paced") return;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      const next = rowsRef.current[cursorRef.current];
      if (next) {
        cursorRef.current += 1;
        setSnapshot((previous) => [...previous, next].slice(-30));
      }
      schedule();
    };
    const schedule = () => {
      timer = setTimeout(tick, broadcastDelay(eventsRef.current, rowsRef.current.length - cursorRef.current));
    };
    schedule();
    return () => clearTimeout(timer);
  }, [playback]);

  return { rows, snapshot, current, broadcastRows, reset };
}
