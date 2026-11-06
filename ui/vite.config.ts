// Copyright (C) 2026 Laurent Millet-Lacombe
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of testsuit.
// testsuit is free software: you can redistribute it and/or modify it under the terms of the GNU Affero
// General Public License as published by the Free Software Foundation, either version 3 of the License,
// or (at your option) any later version.
// testsuit is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
// implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General
// Public License for more details.
// You should have received a copy of the GNU Affero General Public License along with testsuit. If not,
// see <https://www.gnu.org/licenses/>.

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
