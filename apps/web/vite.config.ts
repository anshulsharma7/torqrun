/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI calls the API through Vite's proxy, so the browser sees one origin
// (same as production, where nginx does the proxying).
const apiTarget = process.env.TORQRUN_API_URL ?? "http://127.0.0.1:8000";
const proxy = { "/api": apiTarget, "/healthz": apiTarget, "/readyz": apiTarget };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
