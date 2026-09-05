import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // antd is deliberately NOT pinned to a single manual chunk: with
        // route-level lazy pages (App.tsx React.lazy), letting Rollup
        // auto-split antd modules keeps the initial route loading only
        // the antd subset the shell + dashboard actually use; modules
        // shared by several pages are hoisted into shared chunks. This
        // is the roadmap track-4 fix for the >500 kB antd monolith.
        manualChunks: {
          react: ["react", "react-dom"],
          http: ["axios"],
        },
      },
    },
    chunkSizeWarningLimit: 550,
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
  },
});
