import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5111,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8011" },
  },
  test: {
    environment: "jsdom",
    restoreMocks: true,
    maxWorkers: 1,
    testTimeout: 15000,
  },
});
