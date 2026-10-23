// Build of the sequencer web UI (sequencer README, sections 13 and 14). The output goes into the Python
// package (src/testsuit/sequencer/ui), which tsqd serves under /ui when server.ui is on.
// `npm run dev` serves it with hot reload, the API proxied to a running tsqd (TSQ_URL, default 127.0.0.1:8640).
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const target = process.env.TSQ_URL || "http://127.0.0.1:8640";

export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "../src/testsuit/sequencer/ui",
    emptyOutDir: true,
    chunkSizeWarningLimit: 6000,
  },
  server: {
    proxy: { "/api": { target, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") } },
  },
});
