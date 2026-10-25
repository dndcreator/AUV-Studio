import { afterEach, describe, expect, it, vi } from "vitest";

describe("API location", () => {
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.resetModules(); });

  it("keeps API requests and generated media on the configured backend", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://studio.example/backend/api/");
    vi.resetModules();
    const { api, apiAssetUrl } = await import("./api");
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
    vi.stubGlobal("fetch", fetchMock);
    await api.listWorkflows();
    expect(fetchMock.mock.calls[0][0]).toBe("https://studio.example/backend/api/workflows");
    expect(apiAssetUrl("/api/media/asset")).toBe("https://studio.example/backend/api/media/asset");
    expect(apiAssetUrl("https://cdn.example/image.png")).toBe("https://cdn.example/image.png");
    expect(apiAssetUrl("")).toBe("");
  });
});
