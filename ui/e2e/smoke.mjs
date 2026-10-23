// Smoke test of the built UI against a running tsqd: every page and every editor view renders without a
// JavaScript error. TSQ_URL (default http://127.0.0.1:8640), CHROMIUM (browser path), SHOTS (screenshot folder).
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
  }
}
await browser.close();
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log(`ok: ${n || "all"} views rendered without error`);
