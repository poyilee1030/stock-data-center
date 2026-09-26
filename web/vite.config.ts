import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dashboard is served by the API itself (ADR-0029); in development the
// Vite server forwards /v1 to an API on this machine.
const api = process.env.STOCKDC_API_URL ?? "http://127.0.0.1:28617";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/v1": api } },
  build: { outDir: "dist", assetsDir: "assets", chunkSizeWarningLimit: 1500 },
  test: { include: ["tests/**/*.test.ts"], environment: "node" },
});
