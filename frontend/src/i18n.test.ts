import { describe, expect, it } from "vitest";
import enUS from "./i18n/locales/en-US.json";
import zhCN from "./i18n/locales/zh-CN.json";

function translationKeys(resource: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(resource).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return value && typeof value === "object" && !Array.isArray(value)
      ? translationKeys(value as Record<string, unknown>, path)
      : [path];
  });
}

describe("i18n locale resources", () => {
  it("keeps zh-CN and en-US keys in sync", () => {
    expect(translationKeys(enUS).sort()).toEqual(translationKeys(zhCN).sort());
  });

  it("contains core product shell labels", () => {
    expect(zhCN.subtitle).toBeTruthy();
    expect(enUS.subtitle).toBeTruthy();
    expect(zhCN.run).toBeTruthy();
    expect(enUS.run).toBeTruthy();
  });
});
