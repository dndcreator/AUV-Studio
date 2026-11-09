import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { ActorControl, InputMode, InterventionHistoryItem, InterventionRequest } from "../playerTypes";
import type { RunEvent } from "../types";

type Options = { runId: string | null; active: boolean; text: string; events: RunEvent[];
  onText: (value: string) => void; onNotice: (notice: { type: "success" | "error"; message: string }) => void };

export function usePlayerInterventions(options: Options) {
  const { t } = useTranslation();
  const latest = useRef(options); latest.current = options;
  const [mode, setMode] = useState<InputMode>("director");
  const [actorId, setActorId] = useState("");
  const [pending, setPending] = useState<{ runId: string; requestId: string; mode: InputMode } | null>(null);
  const retry = useRef<{ key: string; id: string } | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [history, setHistory] = useState<InterventionHistoryItem[]>([]);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const status = useQuery({ queryKey: ["player-status", options.runId],
    queryFn: () => api.getInterventionStatus(options.runId!), enabled: !!options.runId && options.active,
    refetchInterval: options.active ? 1500 : false, retry: false });
  const loadHistory = useMutation({
    mutationFn: async (before?: number) => ({ ...(await api.getInterventionHistory(latest.current.runId!, before)), before }),
    onSuccess: data => {
      if (data.run_id !== latest.current.runId) return;
      setHistory(previous => data.before ? [...previous, ...data.items] : data.items); setNextBefore(data.next_before_seq);
    }, onError: () => latest.current.onNotice({ type: "error", message: t("intervention.historyFailed") }),
  });
  const submit = useMutation({
    mutationFn: async (replacement?: { instruction: string; overwrite: boolean }) => {
      const scope = latest.current;
      const requestMode = replacement?.overwrite ? "outcome" : mode;
      if (!scope.runId || requestMode === "director") throw new Error("unavailable");
      const instruction = replacement?.instruction ?? scope.text.trim();
      const key = JSON.stringify([scope.runId, requestMode, actorId, instruction, replacement?.overwrite]);
      if (retry.current?.key !== key) retry.current = { key, id: crypto.randomUUID() };
      const requestId = retry.current.id;
      const current = await api.getInterventionStatus(scope.runId);
      if (!current[requestMode === "action" ? "accepts_actions" : "accepts_outcomes"] || !instruction) throw new Error("unavailable");
      if (current.pending_request_id && current.pending_request_id !== requestId) throw new Error("pending");
      if (requestMode === "action" && (!actorId || current.handoffs[actorId])) throw new Error("actor_required");
      if (!current.pending_request_id) {
        const body: InterventionRequest = { mode: requestMode, instruction, base_state_version: current.state_version,
          request_id: requestId, ...(requestMode === "action" ? { actor_id: actorId } : {}),
          ...(replacement?.overwrite ? { conflict_policy: "overwrite" } : {}) };
        const result = await api.submitIntervention(scope.runId, body);
        return { runId: scope.runId, requestId: result.request_id, mode: requestMode };
      }
      return { runId: scope.runId, requestId, mode: requestMode };
    },
    onSuccess: data => {
      if (data.runId !== latest.current.runId) return;
      setPending(data); latest.current.onText("");
      latest.current.onNotice({ type: "success", message: t(data.mode === "action" ? "intervention.actionQueued" : "intervention.queued") });
      void status.refetch();
    }, onError: () => latest.current.onNotice({ type: "error", message: t("intervention.unavailable") }),
  });
  const control = useMutation({
    mutationFn: async (desired: ActorControl) => {
      const runId = latest.current.runId;
      if (!runId || !actorId) throw new Error("actor_required");
      await api.setActorControl(runId, { actor_id: actorId, control: desired, request_id: crypto.randomUUID() });
      return runId;
    }, onSuccess: (runId, desired) => {
      if (runId !== latest.current.runId) return;
      if (desired === "player_controlled") setMode("action");
      void status.refetch();
    },
    onError: () => latest.current.onNotice({ type: "error", message: t("intervention.unavailable") }),
  });
  useEffect(() => {
    setMode("director"); setActorId(""); setPending(null); retry.current = null;
    setShowHistory(false); setHistory([]); setNextBefore(null);
  }, [options.runId]);
  useEffect(() => { if (!options.active) setMode("director"); }, [options.active]);
  const playerEvents = options.events.filter(event => event.event.startsWith("player_"));
  const lastPlayerEvent = playerEvents[playerEvents.length - 1]?.seq;
  useEffect(() => {
    if (lastPlayerEvent && latest.current.active) void status.refetch();
    if (lastPlayerEvent && showHistory) loadHistory.mutate(undefined);
  }, [lastPlayerEvent, showHistory]); // Event pages, not model calls, drive operator UI refresh.
  useEffect(() => {
    if (!pending || options.runId !== pending.runId) return;
    const event = options.events.find(item => ["player_applied", "player_rejected"].includes(item.event) && item.payload.request_id === pending.requestId);
    if (!event) return;
    const applied = event.event === "player_applied";
    latest.current.onNotice({ type: applied ? "success" : "error", message: applied
      ? t(pending.mode === "action" ? "intervention.actionApplied" : "intervention.applied")
      : t(`intervention.errors.${String(event.payload.code)}`, { defaultValue: t("intervention.failed") }) });
    setPending(null); retry.current = null;
  }, [options.events, options.runId, pending, t]);
  return { mode, setMode, actorId, setActorId, status: status.data, busy: submit.isPending || control.isPending,
    blocked: mode !== "director" && (!!pending || !!status.data?.pending_request_id),
    send: () => submit.mutate(undefined), control: (desired: ActorControl) => control.mutate(desired),
    showHistory, history, historyBusy: loadHistory.isPending, nextBefore,
    openHistory: () => { setShowHistory(true); loadHistory.mutate(undefined); }, closeHistory: () => setShowHistory(false),
    moreHistory: () => { if (nextBefore) loadHistory.mutate(nextBefore); },
    overwrite: (item: InterventionHistoryItem) => {
      if (!window.confirm(t("intervention.confirmOverwrite"))) return;
      setMode("outcome");
      submit.mutate({ instruction: item.instruction, overwrite: true });
    },
  };
}
