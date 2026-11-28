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

// Build of the web UI of the testsuit app (README, "The testsuit app"). The output goes into the Python
// package (src/testsuit/app/ui), which the `testsuit` command serves under /ui.
// `npm run dev` serves it with hot reload, the API proxied to a running testsuit (TESTSUIT_URL, default 127.0.0.1:8650).
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const target = process.env.TESTSUIT_URL || "http://127.0.0.1:8650";

export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "../src/testsuit/app/ui",
    emptyOutDir: true,
    chunkSizeWarningLimit: 6000,
  },
  server: {
    proxy: { "/api": { target, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") } },
  },
});
