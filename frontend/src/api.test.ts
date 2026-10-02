import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

describe("api client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("calls human-response endpoint with payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ run_id: "run_1", status: "queued" })
    });
    vi.stubGlobal("fetch", fetchMock);

    const res = await api.respondHumanCheckpoint("run_1", {
      node_id: "hc1",
      response: "continue",
      responder: "operator",
      metadata: { source: "ui" }
    });

    expect(res).toEqual({ run_id: "run_1", status: "queued" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/runs/run_1/human-response");
    expect(init.method).toBe("POST");
    expect(String(init.body)).toContain("\"node_id\":\"hc1\"");
    expect(String(init.body)).toContain("\"response\":\"continue\"");
  });

  it("throws error when server response is not ok", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      text: async () => "boom"
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.getQueueStatus()).rejects.toThrow("boom");
  });

  it("calls run endpoint with default input payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ run_id: "run_2", status: "queued" })
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.runWorkflow("wf_1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/workflows/wf_1/run");
    expect(String(init.body)).toContain("\"input\":{}");
  });

  it("calls media configuration and generation endpoints", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ media_type: "image", provider: "openai_compatible", base_url: "https://api.example/v1", model: "image-1" })
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ generation_id: "media_1", run_id: "run_1", media_type: "image", provider: "openai_compatible", model: "image-1", status: "queued", assets: [] })
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ generation_id: "media_1", run_id: "run_1", media_type: "image", provider: "openai_compatible", model: "image-1", status: "completed", assets: [] })
      });
    vi.stubGlobal("fetch", fetchMock);

    await api.getMediaConfig("image");
    await api.generateRunMedia("run_1", { media_type: "image", source_text: "script", shot_count: 2 });
    await api.getMediaGeneration("media_1");

    expect((fetchMock.mock.calls[0] as [string])[0]).toContain("/api/media-config/image");
    const [generateUrl, generateInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(generateUrl).toContain("/api/runs/run_1/media");
    expect(generateInit.method).toBe("POST");
    expect(String(generateInit.body)).toContain('"shot_count":2');
    expect((fetchMock.mock.calls[2] as [string])[0]).toContain("/api/media/generations/media_1");
  });

  it("lists and opens saved projects", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ id: "wf_1", name: "Project One", version: 2, updated_at: "2026-08-12T10:00:00Z" }]
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          id: "wf_1",
          name: "Project One",
          version: 2,
          nodes: [],
          edges: [],
          entry_nodes: [],
          environment: { profile: "", scenario: "", facts: [], constraints: [], glossary: {} }
        })
      });
    vi.stubGlobal("fetch", fetchMock);

    const projects = await api.listWorkflows();
    const project = await api.getWorkflow(projects[0].id);
    expect(project.name).toBe("Project One");
    expect((fetchMock.mock.calls[0] as [string])[0]).toContain("/api/workflows");
    expect((fetchMock.mock.calls[1] as [string])[0]).toContain("/api/workflows/wf_1");
  });

  it("calls memory endpoints", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ id: 1, workflow_id: "wf_1", run_id: "run_1", node_id: "a1", role: "planner", content: "x", tags: [], created_at: "2026-01-01T00:00:00Z" }]
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ workflow_id: "wf_1", deleted: 1 })
      });
    vi.stubGlobal("fetch", fetchMock);

    const rows = await api.getMemories("wf_1", 20);
    expect(rows).toHaveLength(1);
    const clear = await api.clearMemories("wf_1");
    expect(clear.deleted).toBe(1);
    expect((fetchMock.mock.calls[0] as [string])[0]).toContain("/api/workflows/wf_1/memories?limit=20");
    expect((fetchMock.mock.calls[1] as [string, RequestInit])[1].method).toBe("DELETE");
  });

  it("calls template compile endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        workflow: { id: "wf_auto_1", name: "x", version: 1, nodes: [], edges: [], entry_nodes: [], environment: { profile: "", scenario: "", facts: [], constraints: [], glossary: {} } },
        extracted: {},
        rationale: "ok",
        simulation_blueprint: { mode: "research", phases: [] }
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const out = await api.compileTemplate({
      plan_text: "market research",
      mode: "research",
      max_agents: 5,
      language: "zh"
    });
    expect(out.workflow.id).toBe("wf_auto_1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/templates/compile");
    expect(init.method).toBe("POST");
  });

  it("calls director command endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        run_id: "run_1",
        accepted: true,
        applied_capability: "set_focus",
        normalized_args: { focus: "x" },
        guidance: "ok",
        event_seq: 12
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const out = await api.sendDirectorCommand("run_1", { text: "focus on young users", scope: "global" });
    expect(out.accepted).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/runs/run_1/director-command");
    expect(init.method).toBe("POST");
  });

  it("calls run stop endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ run_id: "run_1", status: "stopping" })
    });
    vi.stubGlobal("fetch", fetchMock);

    const out = await api.stopRun("run_1");
    expect(out.status).toBe("stopping");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/runs/run_1/stop");
    expect(init.method).toBe("POST");
  });

  it("calls director capabilities endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ capability_id: "set_focus", title: "Set Focus", description: "x", args_schema: { focus: "string" } }]
    });
    vi.stubGlobal("fetch", fetchMock);
    const out = await api.getDirectorCapabilities();
    expect(out[0].capability_id).toBe("set_focus");
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain("/api/director/capabilities");
  });

  it("calls mode contracts endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ mode: "research", description: "x", objectives: [], required_outputs: [], submodes: {} }]
    });
    vi.stubGlobal("fetch", fetchMock);
    const out = await api.getModeContracts();
    expect(out[0].mode).toBe("research");
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain("/api/mode-contracts");
  });

  it("calls model config endpoints without exposing saved key in response", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          provider: "openai_compatible",
          base_url: "https://api.openai.com/v1",
          default_model: "gpt-4o-mini",
          has_api_key: true,
          masked_api_key: "sk-1...abcd",
          source: "database"
        })
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true, message: "Model connection works.", latency_ms: 320 })
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          provider: "openai_compatible",
          base_url: "https://api.openai.com/v1",
          default_model: "gpt-4o-mini",
          has_api_key: true,
          masked_api_key: "sk-1...abcd",
          source: "database"
        })
      });
    vi.stubGlobal("fetch", fetchMock);

    const config = await api.getModelConfig();
    expect(config.has_api_key).toBe(true);
    expect(config).not.toHaveProperty("api_key");

    const test = await api.testModelConfig({
      base_url: "https://api.openai.com/v1",
      default_model: "gpt-4o-mini",
      api_key: "sk-test"
    });
    expect(test.ok).toBe(true);

    await api.saveModelConfig({
      base_url: "https://api.openai.com/v1",
      default_model: "gpt-4o-mini",
      api_key: "sk-test"
    });

    expect((fetchMock.mock.calls[0] as [string])[0]).toContain("/api/model-config");
    expect((fetchMock.mock.calls[1] as [string, RequestInit])[0]).toContain("/api/model-config/test");
    expect((fetchMock.mock.calls[2] as [string, RequestInit])[1].method).toBe("PUT");
  });

  it("calls trace and audit endpoints", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          run_id: "run_1",
          seq: 12,
          event: { seq: 12, run_id: "run_1", node_id: "a1", event: "succeeded", timestamp: "2026-01-01T00:00:00Z", payload: {} },
          parent_events: [],
          caused_by: "node",
          context_snapshot: { node_id: "a1" },
          chain_ok: true
        })
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          run_id: "run_1",
          verified: true,
          total_events: 10,
          checked_events: 10,
          broken_at_seq: null,
          message: "verified 10 events"
        })
      });
    vi.stubGlobal("fetch", fetchMock);

    const trace = await api.getRunTraceEvent("run_1", 12);
    expect(trace.chain_ok).toBe(true);
    const audit = await api.verifyRunAudit("run_1");
    expect(audit.verified).toBe(true);

    expect((fetchMock.mock.calls[0] as [string])[0]).toContain("/api/runs/run_1/trace/12");
    expect((fetchMock.mock.calls[1] as [string])[0]).toContain("/api/runs/run_1/audit/verify");
  });

  it("calls full log endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        run_id: "run_1",
        format: "markdown",
        content: "# Full Simulation Log: run_1",
        event_count: 3,
        node_count: 2
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const fullLog = await api.getFullLog("run_1", "markdown");
    expect(fullLog.event_count).toBe(3);
    expect(fullLog.content).toContain("Full Simulation Log");
    expect((fetchMock.mock.calls[0] as [string])[0]).toContain("/api/runs/run_1/full-log?format=markdown");
  });
});
