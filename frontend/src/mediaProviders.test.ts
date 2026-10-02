import { describe, expect, it } from "vitest";
import { inferMediaPreset, mediaPreset, mediaPresets, presetRequest } from "./mediaProviders";

describe("media provider presets", () => {
  it("keeps image and video protocols separated", () => {
    expect(mediaPreset("image", "openai").provider).toBe("openai_compatible");
    expect(mediaPreset("video", "openai").provider).toBe("openai_video");
    expect(mediaPresets("image")).toHaveLength(3);
  });

  it("builds a complete local adapter request", () => {
    const request = presetRequest("video", mediaPreset("video", "local_adapter"));
    expect(request.provider).toBe("generic_http");
    expect(request.endpoint_path).toBe("/generate");
    expect(request.status_path).toContain("{job_id}");
  });

  it("infers a local adapter from loopback configuration", () => {
    expect(inferMediaPreset({
      media_type: "image",
      provider: "generic_http",
      base_url: "http://127.0.0.1:8188",
      model: "default",
      endpoint_path: "/generate",
      status_path: "/jobs/{job_id}",
      has_api_key: false,
      masked_api_key: "",
      requires_api_key: false,
      source: "database"
    })).toBe("local_adapter");
  });
});
