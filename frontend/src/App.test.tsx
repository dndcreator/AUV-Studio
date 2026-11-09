import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import App from "./App";
import { api } from "./api";
import i18n from "./i18n";
import { useSelectedStore, useWorkflowStore } from "./store";
import type { ModelConfig, PlanCompileResponse, WorkflowDefinition } from "./types";

vi.mock("reactflow", async () => ({ ...await vi.importActual("reactflow"),
  ReactFlow: ({ nodes, onNodeClick, children }: { nodes: Array<{ id: string; data: { actor?: { name: string }; label?: string } }>; onNodeClick?: (event: unknown, node: unknown) => void; children: ReactNode }) =>
    <div data-testid="scene-canvas">{nodes.map(node => <button data-testid={`scene-${node.id}`} key={node.id} onClick={() => onNodeClick?.({}, node)}>{node.data.actor?.name ?? node.data.label}</button>)}{children}</div>,
  Background: () => null, Controls: () => null, MiniMap: () => null,
}));

const ready: ModelConfig = { provider: "openai_compatible", base_url: "https://model.invalid/v1", default_model: "test", has_api_key: true, masked_api_key: "***", requires_api_key: true, source: "database" };
const definition: WorkflowDefinition = { id: "draft_project", name: "Crew survival", version: 1, entry_nodes: ["a"],
  nodes: [{ id: "a", type: "agent", position: { x: 0, y: 0 }, config: { entity_name: "Captain", entity_type: "individual", entity_profile: "Commands the crew." }, inputs: {} },
    { id: "b", type: "agent", position: { x: 400, y: 0 }, config: { entity_name: "Engineer", entity_type: "individual", entity_profile: "Checks life support." }, inputs: {} }],
  edges: [{ id: "ab", source: "a", target: "b" }], environment: { profile: "", scenario: "A damaged ship.", facts: [], constraints: [], glossary: {}, simulation: { ui_mode: "roleplay" } } };
const draft: PlanCompileResponse = { workflow: definition, extracted: {}, rationale: "", simulation_blueprint: { mode: "roleplay", submode: "none", goal: "Stabilize life support." } };

beforeEach(async () => {
  await i18n.changeLanguage("en-US");
  window.localStorage.clear();
  useSelectedStore.setState({ selectedNodeId: null, selectedEdgeId: null });
  useWorkflowStore.getState().loadWorkflow({ ...structuredClone(definition), id: "original", name: "Original", nodes: [], edges: [] });
  vi.spyOn(api, "getNodeSpecs").mockResolvedValue([]);
  vi.spyOn(api, "getInteractionModes").mockResolvedValue([]);
  vi.spyOn(api, "getDirectorCapabilities").mockResolvedValue([]);
  vi.spyOn(api, "getModelConfig").mockResolvedValue(ready);
  vi.spyOn(api, "listTemplates").mockResolvedValue([]);
  vi.spyOn(api, "listWorkflows").mockResolvedValue([]);
  vi.spyOn(api, "getMemories").mockResolvedValue([]);
  vi.spyOn(api, "getObservability").mockRejectedValue(new Error("not requested"));
  vi.spyOn(api, "getQueueStatus").mockRejectedValue(new Error("not requested"));
  vi.spyOn(api, "verifyRunAudit").mockRejectedValue(new Error("not requested"));
  vi.spyOn(api, "getRunMetrics").mockRejectedValue(new Error("not requested"));
  vi.spyOn(api, "getRunCheckpoints").mockResolvedValue({ run_id: "r", checkpoints: [] });
  vi.spyOn(api, "compileTemplate").mockImplementation(async () => structuredClone(draft));
  vi.spyOn(api, "updateWorkflow").mockImplementation(async workflow => workflow);
  vi.spyOn(api, "runWorkflow").mockResolvedValue({ run_id: "r", status: "running" });
  vi.spyOn(api, "getRunStatus").mockResolvedValue({ run_id: "r", status: "running" });
  vi.spyOn(api, "getRunEvents").mockResolvedValue({ run_id: "r", events: [] });
  vi.spyOn(api, "stopRun").mockResolvedValue({ run_id: "r", status: "stopping" });
  vi.spyOn(api, "sendDirectorCommand").mockResolvedValue({ run_id: "r", accepted: true, applied_capability: "guidance", normalized_args: {}, guidance: "Instruction received." });
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("Unexpected network in offline journey test"))));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function mount() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })}><App /></QueryClientProvider>);
}
async function generate() {
  // Wait until the saved provider has been loaded before submitting.
  await waitFor(() => expect(api.getModelConfig).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Simulation request"), { target: { value: "A crew faces a life-support failure." } });
  fireEvent.click(screen.getByRole("button", { name: "Create scene" }));
  await screen.findByRole("heading", { name: "Review scene" });
}

describe("integrated studio journey", () => {
  it("sends an actor attempt through the gateway and allows control handoff without replacing Director guidance", async () => {
    vi.mocked(api.compileTemplate).mockResolvedValue({ ...structuredClone(draft), workflow: { ...structuredClone(definition),
      environment: { ...definition.environment, simulation: { execution_model: "continuous" } } } });
    const controls: Record<string, "autonomous" | "player_controlled"> = {};
    vi.spyOn(api, "getInterventionStatus").mockImplementation(async () => ({ run_id: "r", state_version: 3,
      enabled: true, accepts_actions: true, accepts_outcomes: true, pending_request_id: null, controls: { ...controls }, handoffs: {}, waiting_actor_ids: [] }));
    vi.spyOn(api, "setActorControl").mockImplementation(async (_run, body) => { controls[body.actor_id] = body.control; return { request_id: body.request_id, status: "handoff" }; });
    vi.spyOn(api, "submitIntervention").mockResolvedValue({ request_id: "try", request_seq: 3, status: "queued" });
    mount(); await generate();
    fireEvent.click(screen.getByRole("button", { name: "Confirm & start" }));
    fireEvent.change(await screen.findByLabelText("Input mode"), { target: { value: "action" } });
    fireEvent.change(screen.getByLabelText("Acting character"), { target: { value: "a" } });
    fireEvent.click(screen.getByRole("button", { name: "Take over" }));
    await screen.findByRole("button", { name: "Return control" });
    fireEvent.change(screen.getByLabelText("Attempt action"), { target: { value: "Try to repair the hatch." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.submitIntervention).toHaveBeenCalledWith("r", expect.objectContaining({ mode: "action", actor_id: "a", base_state_version: 3 })));
    fireEvent.change(screen.getByLabelText("Input mode"), { target: { value: "director" } });
    expect(screen.getByLabelText("Message the Director")).not.toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Return control" }));
    await waitFor(() => expect(api.setActorControl).toHaveBeenLastCalledWith("r", expect.objectContaining({ actor_id: "a", control: "autonomous" })));
  });
  it("opens intervention history and submits an explicitly confirmed current-fact override", async () => {
    vi.mocked(api.compileTemplate).mockResolvedValue({ ...structuredClone(draft), workflow: { ...structuredClone(definition),
      environment: { ...definition.environment, simulation: { execution_model: "continuous" } } } });
    vi.spyOn(api, "getInterventionStatus").mockResolvedValue({ run_id: "r", state_version: 9, enabled: true,
      accepts_actions: true, accepts_outcomes: true, pending_request_id: null, controls: {}, handoffs: {}, waiting_actor_ids: [] });
    vi.spyOn(api, "submitIntervention").mockResolvedValue({ request_id: "overwrite", request_seq: 40, status: "queued" });
    vi.spyOn(api, "getInterventionHistory").mockResolvedValue({ run_id: "r", next_before_seq: null, items: [{
      request_seq: 20, request_id: "old", mode: "outcome", instruction: "The hatch is open.", timestamp: "2026-11-06T00:00:00Z",
      status: "rejected", code: "outcome_conflict", event_seq: 21, before_checkpoint_seq: 19, after_checkpoint_seq: null,
    }] });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mount(); await generate();
    fireEvent.click(screen.getByRole("button", { name: "Confirm & start" }));
    fireEvent.click(await screen.findByRole("button", { name: "Interventions" }));
    const history = await screen.findByRole("dialog", { name: "Interventions" });
    fireEvent.click(await within(history).findByRole("button", { name: "Override current facts" }));
    await waitFor(() => expect(api.submitIntervention).toHaveBeenCalledWith("r", expect.objectContaining({
      mode: "outcome", instruction: "The hatch is open.", base_state_version: 9, conflict_policy: "overwrite",
    })));
    expect(window.confirm).toHaveBeenCalledOnce();
  });
  it("queues a world outcome separately from Director guidance without claiming it already applied", async () => {
    vi.mocked(api.compileTemplate).mockResolvedValue({ ...structuredClone(draft), workflow: { ...structuredClone(definition),
      environment: { ...definition.environment, simulation: { ui_mode: "roleplay", execution_model: "continuous" } } } });
    vi.spyOn(api, "getInterventionStatus").mockResolvedValue({ run_id: "r", state_version: 7, enabled: true,
      accepts_outcomes: true, accepts_actions: true, controls: {}, handoffs: {}, waiting_actor_ids: [], pending_request_id: null });
    vi.spyOn(api, "submitIntervention").mockResolvedValue({ request_id: "queued", request_seq: 20, status: "queued" });
    mount(); await generate();
    fireEvent.click(screen.getByRole("button", { name: "Confirm & start" }));
    const mode = await screen.findByLabelText("Input mode");
    fireEvent.change(mode, { target: { value: "outcome" } });
    fireEvent.change(screen.getByLabelText("World outcome"), { target: { value: "The engineer reaches the engine room." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.submitIntervention).toHaveBeenCalledWith("r", expect.objectContaining({
      mode: "outcome", instruction: "The engineer reaches the engine room.", base_state_version: 7,
    })));
    expect(api.sendDirectorCommand).not.toHaveBeenCalled();
    expect(await screen.findByText("Outcome queued")).toBeInTheDocument();
    expect(screen.queryByText("Outcome applied")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Stop/ }));
    await waitFor(() => expect(api.stopRun).toHaveBeenCalledWith("r"));
  });
  it("shows a later version conflict and releases the outcome input for correction", async () => {
    vi.mocked(api.compileTemplate).mockResolvedValue({ ...structuredClone(draft), workflow: { ...structuredClone(definition),
      environment: { ...definition.environment, simulation: { execution_model: "continuous" } } } });
    vi.spyOn(api, "getInterventionStatus").mockResolvedValue({ run_id: "r", state_version: 1, enabled: true,
      accepts_outcomes: true, accepts_actions: true, controls: {}, handoffs: {}, waiting_actor_ids: [], pending_request_id: null });
    let queued = false;
    vi.spyOn(api, "submitIntervention").mockImplementation(async () => {
      queued = true; return { request_id: "conflict", request_seq: 21, status: "queued" };
    });
    vi.mocked(api.getRunEvents).mockImplementation(async () => ({ run_id: "r", events: queued ? [{
      seq: 22, run_id: "r", node_id: "player", event: "player_rejected", timestamp: "2026-11-06T00:00:00Z",
      payload: { request_id: "conflict", code: "state_version_conflict" },
    }] : [] }));
    mount(); await generate();
    fireEvent.click(screen.getByRole("button", { name: "Confirm & start" }));
    fireEvent.change(await screen.findByLabelText("Input mode"), { target: { value: "outcome" } });
    fireEvent.change(screen.getByLabelText("World outcome"), { target: { value: "Move the engineer." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("The world changed. Review it and submit again.", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByLabelText("World outcome")).not.toBeDisabled();
    expect(screen.queryByText("Outcome applied")).not.toBeInTheDocument();
  });
  it("generates visuals only on request and keeps them separate from actors and execution", async () => {
    const generateVisual = vi.spyOn(api, "generatePresentation").mockResolvedValue({ version: 1, renderer: "spatial", theme: { accent: "#FF8800" },
      regions: [{ id: "room", label: "Room", x: 0, y: 0, width: 600, height: 400 }], placements: [], assets: [] });
    mount(); await generate();
    expect(generateVisual).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Generate visual scene" }));
    await waitFor(() => expect(document.querySelector(".scene-region")).toBeInTheDocument());
    expect(api.runWorkflow).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().workflow.id).toBe("original");
    fireEvent.click(screen.getByRole("button", { name: "Apply & open editor" }));
    await waitFor(() => expect(useWorkflowStore.getState().workflow.id).toBe("draft_project"));
    expect(useWorkflowStore.getState().nodes).toHaveLength(2);
    expect(useWorkflowStore.getState().workflow.environment.simulation?.presentation).toHaveProperty("renderer", "spatial");
  });
  it("goes from a quiet home to a staged draft, then starts, intervenes and stops through existing APIs", async () => {
    mount();
    expect(screen.getByRole("heading", { name: "What would you like to simulate?" })).toBeInTheDocument();
    expect(screen.queryByLabelText("close-inspector-panel")).not.toBeInTheDocument();
    await generate();
    expect(useWorkflowStore.getState().workflow.id).toBe("original");
    expect(api.runWorkflow).not.toHaveBeenCalled();
    expect(screen.getByTestId("scene-a")).toHaveTextContent("Captain");
    fireEvent.click(screen.getByRole("button", { name: "Confirm & start" }));
    await waitFor(() => expect(api.runWorkflow).toHaveBeenCalledWith("draft_project", expect.any(Object)));
    expect(useWorkflowStore.getState().workflow.id).toBe("draft_project");
    fireEvent.change(screen.getByLabelText("Message the Director"), { target: { value: "Reassess the oxygen leak." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.sendDirectorCommand).toHaveBeenCalledWith("r", expect.objectContaining({ text: "Reassess the oxygen leak." })));
    fireEvent.click(screen.getByRole("button", { name: /Stop/ }));
    await waitFor(() => expect(api.stopRun).toHaveBeenCalledWith("r"));
  });

  it("recompiles explicit role revisions without starting or leaving stale identity prompts in use", async () => {
    mount(); await generate();
    fireEvent.click(screen.getByTestId("scene-a"));
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "A cautious captain with limited information." } });
    fireEvent.click(screen.getByRole("button", { name: "Update plan" }));
    await waitFor(() => expect(api.compileTemplate).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.compileTemplate).mock.calls[1][0].plan_text).toContain("A cautious captain with limited information.");
    expect(api.runWorkflow).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().workflow.id).toBe("original");
  });

  it("applies a draft to the full editor without starting, and keeps templates and advanced controls reachable", async () => {
    mount(); await generate();
    fireEvent.click(screen.getByRole("button", { name: "Apply & open editor" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Editor" })).toHaveAttribute("aria-pressed", "true"));
    expect(api.runWorkflow).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().nodes).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Scene" }));
    expect(screen.getByRole("heading", { name: "Scene" })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Project actions"));
    const menu = screen.getByLabelText("Project actions").parentElement!;
    fireEvent.click(within(menu).getByRole("button", { name: "Templates" }));
    expect(screen.getByRole("button", { name: "Editor" })).toHaveAttribute("aria-pressed", "true");
  });

  it("handles planning failures without replacing the previous project or starting a run", async () => {
    vi.mocked(api.compileTemplate).mockRejectedValue(new Error("Provider unavailable"));
    mount();
    await waitFor(() => expect(api.getModelConfig).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Simulation request"), { target: { value: "A small scene." } });
    fireEvent.click(screen.getByRole("button", { name: "Create scene" }));
    expect(await screen.findByText("Provider unavailable")).toBeInTheDocument();
    expect(useWorkflowStore.getState().workflow.id).toBe("original");
    expect(api.runWorkflow).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Simulation request")).toHaveValue("A small scene.");
  });

  it("collects the missing model configuration before generating and preserves the original request", async () => {
    vi.mocked(api.getModelConfig).mockResolvedValue({ ...ready, has_api_key: false });
    vi.spyOn(api, "saveModelConfig").mockResolvedValue(ready);
    mount();
    await waitFor(() => expect(api.getModelConfig).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Simulation request"), { target: { value: "Keep this original request." } });
    fireEvent.click(screen.getByRole("button", { name: "Create scene" }));
    const setup = await screen.findByRole("dialog", { name: "Model Setup" });
    expect(api.compileTemplate).not.toHaveBeenCalled();
    fireEvent.change(within(setup).getByLabelText("API Key"), { target: { value: "offline-test-key" } });
    fireEvent.click(within(setup).getByRole("button", { name: "Save" }));
    await screen.findByRole("heading", { name: "Review scene" });
    expect(api.compileTemplate).toHaveBeenCalledWith(expect.objectContaining({ plan_text: "Keep this original request." }));
    expect(api.runWorkflow).not.toHaveBeenCalled();
  });

  it("retains research evidence confirmation in the one-stop journey", async () => {
    vi.mocked(api.compileTemplate).mockResolvedValue({ ...structuredClone(draft), simulation_blueprint: { mode: "research", submode: "research" } });
    mount(); await generate();
    const evidence = await screen.findByRole("dialog", { name: "Add Research Evidence" });
    expect(api.runWorkflow).not.toHaveBeenCalled();
    fireEvent.click(within(evidence).getByRole("button", { name: "Continue Without Evidence" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm & start" }));
    await waitFor(() => expect(api.runWorkflow).toHaveBeenCalledTimes(1));
  });

  it("shows a human decision and resumes through the existing checkpoint API", async () => {
    vi.mocked(api.getRunStatus).mockResolvedValue({ run_id: "r", status: "waiting_human" });
    vi.mocked(api.getRunEvents).mockResolvedValue({ run_id: "r", events: [{ seq: 1, run_id: "r", node_id: "a", event: "waiting_human", timestamp: "2026-11-01T00:00:00Z", payload: { output: { question: "Seal the compartment?" } } }] });
    vi.spyOn(api, "respondHumanCheckpoint").mockResolvedValue({ run_id: "r", status: "running" });
    mount(); await generate();
    fireEvent.click(screen.getByRole("button", { name: "Confirm & start" }));
    await screen.findAllByText("Seal the compartment?");
    fireEvent.change(screen.getByLabelText("Your decision"), { target: { value: "Seal it after evacuation." } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm & continue" }));
    await waitFor(() => expect(api.respondHumanCheckpoint).toHaveBeenCalledWith("r", expect.objectContaining({ node_id: "a", response: "Seal it after evacuation." })));
  });
});
