/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Separate from vite.config.ts on purpose: `vite build` must stay governed by
// the production chunking config, while `vitest` needs the jsdom environment
// and a setup file. Shared by both is `npm run test`.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    // Measured here: an antd page mount costs ~0.4s in jsdom and every click
    // that reveals new antd markup (tab pane, drawer, popconfirm) ~1s, because
    // jsdom parses the ~700 CSS rules antd's cssinjs injects at that moment.
    // The 5s default therefore fails a test that is merely slow, not wrong.
    // This ceiling only bounds hangs: findBy*/waitFor still give up after RTL's
    // own 1s default, so no assertion is relaxed by it.
    testTimeout: 20000,
  },
});
