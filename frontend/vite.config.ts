import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const backend = process.env.TWINPILOT_BACKEND ?? "http://127.0.0.1:8770";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5290,
    strictPort: true,
    proxy: {
      "/api": backend,
      "/ws": { target: backend.replace("http", "ws"), ws: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 1500,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    globals: true,
  },
});
