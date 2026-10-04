import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          flow: ["reactflow"],
          query: ["@tanstack/react-query"],
          i18n: ["i18next", "react-i18next"]
        }
      }
    }
  },
  server: {
    port: 5173
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/setupTests.ts",
    globals: true,
    clearMocks: true
  }
});
