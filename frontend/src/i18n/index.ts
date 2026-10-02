import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import type { Lang } from "../appTypes";
import enUS from "./locales/en-US.json";
import zhCN from "./locales/zh-CN.json";

const STORAGE_KEY = "auv.lang";

function normalizeLanguage(raw: string | null | undefined): Lang {
  if (raw?.toLowerCase().startsWith("en")) {
    return "en-US";
  }
  return "zh-CN";
}

function initialLanguage(): Lang {
  if (typeof window === "undefined") {
    return "zh-CN";
  }
  return normalizeLanguage(window.localStorage.getItem(STORAGE_KEY) ?? window.navigator.language);
}

void i18n.use(initReactI18next).init({
  resources: {
    "zh-CN": { translation: zhCN },
    "en-US": { translation: enUS }
  },
  lng: initialLanguage(),
  fallbackLng: "zh-CN",
  interpolation: {
    escapeValue: false
  },
  returnNull: false
});

i18n.on("languageChanged", (lng: string) => {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, normalizeLanguage(lng));
  }
});

export default i18n;
