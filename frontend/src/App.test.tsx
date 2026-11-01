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
