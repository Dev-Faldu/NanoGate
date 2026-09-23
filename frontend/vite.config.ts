import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const gateway = process.env.NANOGATE_URL ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: gateway, changeOrigin: false },
      "/v1": { target: gateway },
      "/healthz": { target: gateway },
      "/readyz": { target: gateway },
      "/metrics": { target: gateway },
      "/docs": { target: gateway },
      "/openapi.json": { target: gateway },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1200 },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], include: ["src/**/*.test.{ts,tsx}"] },
} as any);
