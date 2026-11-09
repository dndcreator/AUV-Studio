// Node 24's native TypeScript stripping runs the actual frontend projection, without a second implementation.
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { buildSceneFrame } from "../frontend/src/scene/presentation.ts";

const root = new URL("../", import.meta.url);
const path = new URL("backend/evals/results/behavior_latest.json", root);
const artifact = JSON.parse(fs.readFileSync(path, "utf8"));
const cases = JSON.parse(fs.readFileSync(new URL("backend/evals/visual_cases.json", root), "utf8")).cases;
if (!artifact.samples.length || artifact.samples.some(row => row.capability !== "visual")) {
  throw new Error("The latest record is not a visual evaluation; nothing overwritten.");
}
for (const row of artifact.samples) {
  if (!row.output?.presentation) continue;
  const input = cases.find(c => c.id === row.case_id)?.input;
  if (!input) throw new Error("Visual case missing");
  const workflow = structuredClone(input.workflow);
  workflow.environment.simulation = { presentation: row.output.presentation };
  const destination = input.destination;
  const action = { seq: 3, run_id: "offline-projection-probe", node_id: "a", event: "succeeded",
    timestamp: "2026-11-01T00:00:00Z", payload: { output: { action: `A moves to ${destination} and activates the alarm.` } } };
  const state = { seq: 4, run_id: action.run_id, node_id: "state_coder", event: "dynamic_state_updated", timestamp: action.timestamp,
    payload: { coding: { status: "coded" }, dynamic_state: { concepts: input.state.concepts,
      values: Object.fromEntries([["position", destination], ["alarm", "on"]].map(([id, value]) => [id,
        { value, evidence_kind: "action", source_refs: [{ node_id: "a", action_index: 1, event_seq: 3 }] }])) } } };
  const before = buildSceneFrame(workflow, []);
  const after = buildSceneFrame(workflow, [action, state]);
  const placement = after.presentation.placements.find(p => p.node_id === "a");
  const region = after.presentation.regions.find(r => r.id === placement?.region_id);
  const color = after.presentation.bindings?.find(b => b.kind === "color" && b.concept_id === "alarm");
  const original = before.presentation.primitives?.find(p => p.id === color?.target_id);
  const updated = after.presentation.primitives?.find(p => p.id === color?.target_id);
  const rejected = structuredClone(state); rejected.payload.coding.status = "pending";
  const privateState = structuredClone(state); privateState.payload.dynamic_state.concepts.position.visibility = "private";
  const checks = {
    location_matches_destination: region?.label === destination,
    color_binding_applied: !!color && updated?.color === color.values.on,
    color_visibly_changes: !!updated && updated.color !== original?.color,
    pending_update_ignored: JSON.stringify(buildSceneFrame(workflow, [action, rejected]).presentation) === JSON.stringify(before.presentation),
    missing_source_ignored: JSON.stringify(buildSceneFrame(workflow, [state]).presentation) === JSON.stringify(before.presentation),
    private_location_ignored: JSON.stringify(buildSceneFrame(workflow, [action, privateState]).presentation.placements) === JSON.stringify(before.presentation.placements),
    replay_restores_initial: JSON.stringify(buildSceneFrame(workflow, [action]).presentation) === JSON.stringify(before.presentation),
  };
  row.runtime_probe = { scope: "actual generated layout with synthetic committed-event fixtures, not a real simulation", checks };
  const required = Object.entries(checks).filter(([key]) => key !== "color_visibly_changes");
  row.runtime_probe.status = required.some(([, value]) => !value) ? "failed" : checks.color_visibly_changes ? "passed" : "review_required";
  console.log(row.case_id, JSON.stringify(checks));
}
fs.writeFileSync(path, JSON.stringify(artifact, null, 2) + "\n");
console.log(`Updated ${fileURLToPath(path)}; semantic acceptance unchanged.`);
process.exitCode = artifact.samples.some(row => row.runtime_probe?.status === "failed") ? 1
  : artifact.samples.some(row => !row.runtime_probe || row.runtime_probe.status === "review_required") ? 3 : 0;
