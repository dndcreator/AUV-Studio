export type InputMode = "director" | "action" | "outcome";
export type ActorControl = "autonomous" | "player_controlled";
export type PlayerStatus = {
  run_id: string; state_version: number; enabled: boolean; accepts_outcomes: boolean; accepts_actions: boolean;
  pending_request_id: string | null; controls: Record<string, ActorControl>; handoffs: Record<string, ActorControl>;
  waiting_actor_ids: string[];
};
export type InterventionRequest = {
  mode: "action" | "outcome"; instruction: string; actor_id?: string; base_state_version: number;
  request_id: string; conflict_policy?: "reject" | "overwrite";
};
export type InterventionHistoryItem = {
  request_seq: number; request_id: string; mode: "action" | "outcome" | "control"; actor_id?: string;
  instruction: string; control?: ActorControl; timestamp: string; status: "queued" | "running" | "applied" | "rejected";
  code?: string; event_seq: number; before_checkpoint_seq: number | null; after_checkpoint_seq: number | null;
};
export type InterventionHistory = { run_id: string; items: InterventionHistoryItem[]; next_before_seq: number | null };
