import { describe, expect, it } from "vitest";
import enUS from "./i18n/locales/en-US.json";
import zhCN from "./i18n/locales/zh-CN.json";

describe("i18n locale resources", () => {
  it("keeps zh-CN and en-US keys in sync", () => {
    expect(Object.keys(enUS).sort()).toEqual(Object.keys(zhCN).sort());
  });

  it("contains core product shell labels", () => {
    expect(zhCN.subtitle).toBeTruthy();
    expect(enUS.subtitle).toBeTruthy();
    expect(zhCN.run).toBeTruthy();
    expect(enUS.run).toBeTruthy();
  });
});
