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

// Smoke test of the built UI against a running tsqd: every page and every editor view renders without a
// JavaScript error. TSQ_URL (default http://127.0.0.1:8640), CHROMIUM (browser path), SHOTS (screenshot folder),
// MXP_EDIT_URL (a running `mxp edit`: its criteria editor, unit tests run from the page).
import { chromium } from "playwright-core";
import fs from "node:fs";

const base = (process.env.TSQ_URL || "http://127.0.0.1:8640").replace(/\/$/, "");
const shots = process.env.SHOTS;
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
const errors = [];
page.on("pageerror", (e) => errors.push(`${page.url()}: ${e.message}`));
page.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && errors.push(`${page.url()}: ${m.text()}`));
const api = async (path, init) => (await fetch(`${base}/${path}`, init)).json();

let n = 0;
async function visit(hash, wait = 1200, name) {
  await page.goto(`${base}/ui/#/${hash}`);
  await page.waitForTimeout(wait);
  if (shots) {
    fs.mkdirSync(shots, { recursive: true });
    await page.screenshot({ path: `${shots}/${String(++n).padStart(2, "0")}-${name || hash.replace(/[^a-z0-9]+/gi, "_").slice(0, 60)}.png` });
  }
}

for (const p of ["queue", "runs", "lab", "config"]) await visit(p);
const runs = await api("runs");
const list = Array.isArray(runs) ? runs : runs.runs || [];
if (list.length) for (const tab of ["pipeline", "steps", "files"]) await visit(`runs/${list[0].id}?tab=${tab}`, 1500);

const edit = await api("edit").catch(() => null);
if (edit?.enabled) {
  await visit("edit");
  await visit("datapacks", 4000);
  let sessions = edit.sessions || [];
  if (!sessions.length)
    for (const r of edit.repositories)
      sessions.push(await api("edit/sessions", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ repo: r.id }) }));
  for (const s of sessions) {
    await visit(`edit/${s.id}`, 1500);
    const files = await api(`edit/sessions/${s.id}/files`);
    const seen = new Set();
    for (const f of files) {
      if (f.kind === "other" || seen.has(f.kind)) continue;
      seen.add(f.kind);
      const views = { testdef: ["form", "pipeline", "yaml"], subpipelines: ["pipeline"], template: ["template"] }[f.kind] || ["form", "yaml"];
      for (const v of views) await visit(`edit/${s.id}?file=${encodeURIComponent(f.path)}&view=${v}`, 2500, `${s.kind}-${f.kind}-${v}`);
    }
    // the criteria editor on the session: a scenario file, then a utests file with its unit tests panel
    const criteria = await api(`edit/sessions/${s.id}/criteria`);
    for (const sc of criteria.scenarios || []) {
      await visit(`criteria/${s.id}?file=${encodeURIComponent(sc.files[0])}`, 3000, `${s.kind}-criteria-${sc.name}`);
      for (const u of sc.utests) await visit(`criteria/${s.id}?file=${encodeURIComponent(u)}`, 3000, `${s.kind}-utests-${sc.name}`);
    }
  }
}

// mxp edit: the same UI in its criteria mode (MXP_EDIT_URL, a running `mxp edit`)
if (process.env.MXP_EDIT_URL) {
  const mxp = process.env.MXP_EDIT_URL.replace(/\/$/, "");
  const overview = await (await fetch(`${mxp}/criteria`)).json();
  const scenario = overview.scenarios.find((sc) => sc.utests.length) || overview.scenarios[0];
  await page.goto(`${mxp}/ui/#/criteria?file=${encodeURIComponent(scenario.files[0])}`);
  await page.waitForSelector(".crit-tree .tree-row", { timeout: 20000 });
  await page.waitForSelector(".monaco-editor", { timeout: 20000 });
  if (scenario.utests.length) {
    // run the unit tests, as a user would, and wait for the matrix
    await page.click("text=Run all");
    await page.waitForSelector(".utests-bar .badge", { timeout: 300000 });
    const failing = await page.$$eval(".utests-matrix td.cell.bad", (cells) => cells.length);
    if (failing) errors.push(`mxp edit: ${failing} unit-test cell(s) not as expected`);
  }
  if (shots) await page.screenshot({ path: `${shots}/${String(++n).padStart(2, "0")}-mxp-edit.png` });
  // a data file of a case: its table and plot
  const csv = overview.utests.flatMap((u) => (u.cases || []).flatMap((c) => c.data_files)).find((f) => /\.csv$/.test(f));
  if (csv) {
    await page.goto(`${mxp}/ui/#/criteria?file=${encodeURIComponent(csv)}`);
    await page.waitForSelector(".data-plot", { timeout: 20000 });
    if (shots) await page.screenshot({ path: `${shots}/${String(++n).padStart(2, "0")}-mxp-edit-data.png` });
  }
}
await browser.close();
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log(`ok: ${n || "all"} views rendered without error`);
