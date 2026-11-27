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

// The Extract page of `mxp webui` (#/extract): pick data files or folders, tick the parameters to keep (quick
// search, "select all" on what the search shows), and write them to an HDF5 file (routes /extract,
// testsuit.exploit.editor.extract), with a progress bar. The data are listed by the Plot routes (/plot/sources).
// CSV output and other sources of parameters (an InfluxDB query, as a pseudo-file) are not there yet.
import { useEffect, useMemo, useState } from "react";
import { get, post, url } from "../api";
import { PathList, PathPicker, type Info, type Root } from "../exploit/ExploitPage";
import { classes, notify } from "../util";

interface Sources { sources: { label: string; path: string }[]; fields: string[]; total: number; truncated: boolean }
interface Job { id: string; state: "running" | "done" | "failed"; phase: string; done: number; total: number; output: string; size: number | null; error: string | null }

const MAX_LISTED = 500;
const paramOf = (f: string) => (f.includes("::") ? f.slice(f.indexOf("::") + 2) : f);
const count = (n: number) => n.toLocaleString("en-US");
const bytes = (n: number) => (n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(1)} KB` : n < 1 << 30 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${(n / (1 << 30)).toFixed(2)} GB`);

function matcher(filter: string): (field: string) => boolean {
  const f = filter.trim();
  if (!f) return () => true;
  try { const r = new RegExp(f, "i"); return (x) => r.test(x); } catch { /* words */ }
  const low = f.toLowerCase();
  return (x) => x.toLowerCase().includes(low);
}

export function ExtractPage() {
  const [info, setInfo] = useState<Info | null>(null);
  const [sources, setSources] = useState<string[]>([]);
  const [found, setFound] = useState<Sources | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  const [picker, setPicker] = useState(false);
  const [filter, setFilter] = useState("");
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [output, setOutput] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [folder, setFolder] = useState<string | null>(null);      // set: the server decides where files are written (tsqd)
  const roots: Root[] = info?.roots || [];

  useEffect(() => { get<Info>("exploit").then(setInfo).catch(() => setInfo(null)); }, []);
  useEffect(() => { get<{ folder: string | null }>("extract/info").then((i) => setFolder(i.folder)).catch(() => undefined); }, []);
  useEffect(() => {
    setFound(null); setError(null); setTicked(new Set());
    if (!sources.length) return;
    setOpening(true);
    post<Sources>("plot/sources", { paths: sources }).then(setFound).catch((e) => setError(e.message)).finally(() => setOpening(false));
    if (!output) setOutput(folder ? sources[0].split(/[\\/]/).filter(Boolean).pop()!.replace(/\.[^.]*$/, "") + "_params.h5" : sources[0].replace(/[\\/]+$/, "").replace(/\.[^./\\]*$/, "") + "_params.h5");
  }, [sources]);

  const match = useMemo(() => matcher(filter), [filter]);
  const matching = useMemo(() => (found?.fields || []).filter(match), [found, match]);
  const toggle = (f: string) => setTicked((t) => { const n = new Set(t); if (n.has(f)) n.delete(f); else n.add(f); return n; });
  const tickAll = (on: boolean) => setTicked((t) => { const n = new Set(t); matching.forEach((f) => (on ? n.add(f) : n.delete(f))); return n; });
  const allTicked = matching.length > 0 && matching.every((f) => ticked.has(f));

  const running = job?.state === "running";
  const start = async () => {
    try { setJob(await post<Job>("extract/start", { paths: sources, fields: [...ticked], output, overwrite })); }
    catch (e: any) { notify(e.message, "error"); }
  };
  useEffect(() => {
    if (!running || !job) return;
    const timer = setInterval(() => get<Job>(`extract/${job.id}`).then(setJob).catch(() => undefined), 500);
    return () => clearInterval(timer);
  }, [running, job?.id]);
  const percent = job ? Math.round((100 * job.done) / Math.max(1, job.total)) : 0;

  return (
    <div className="plot-page">
      <aside className="plot-side">
        <section className="card plot-section">
          <div className="card-head"><strong>1. Input</strong><span className="muted small">files or folders, any format</span>
            <span style={{ flex: 1 }} /><button className="small" disabled={running} onClick={() => setPicker(true)}>Browse…</button></div>
          <PathList paths={sources} roots={roots} onRemove={(p) => setSources((s) => s.filter((x) => x !== p))}
            empty="Pick recordings: TDMS, MDF, CSV, HDF5, DXD… or a folder of them." />
          {opening && <p className="muted small">Opening…</p>}
          {error && <div className="banner error">{error}</div>}
        </section>
        <section className="card plot-section plot-fields">
          <div className="card-head"><strong>2. Parameters</strong>
            <span className="muted small">{found ? `${count(ticked.size)} picked of ${count(found.total)}` : "of the input"}</span></div>
          <input className="mono" value={filter} onChange={(e) => setFilter(e.target.value)} disabled={!found}
            placeholder="search: words or regex" />
          <div className="plot-field-list">
            {!found && <p className="muted small">Pick the input first.</p>}
            {found && !matching.length && <p className="muted small">No parameter matches.</p>}
            {matching.slice(0, MAX_LISTED).map((f) => (
              <label key={f} className={classes("plot-field", ticked.has(f) && "selected")} title={f}>
                <input type="checkbox" checked={ticked.has(f)} disabled={running} onChange={() => toggle(f)} />
                <span className="mono">{found!.sources.length > 1 ? f : paramOf(f)}</span>
              </label>
            ))}
            {matching.length > MAX_LISTED && <p className="muted small">{count(matching.length - MAX_LISTED)} more: refine the search.</p>}
          </div>
          <div className="plot-field-actions">
            <button className="small" disabled={!matching.length || running} onClick={() => tickAll(!allTicked)}
              title="Tick (or untick) every parameter the search shows, the ones listed and beyond">{allTicked ? "Untick" : "Select all"} {count(matching.length)}</button>
            {ticked.size > 0 && <button className="small" disabled={running} onClick={() => setTicked(new Set())}>Clear</button>}
          </div>
        </section>
      </aside>
      <section className="plot-main">
        <div className="card plot-section">
          <div className="card-head"><strong>3. Extract to HDF5</strong></div>
          <label className="small muted">{folder ? `Output file name (kept on the server in ${folder}; download it once done)` : "Output file"}</label>
          <input className="mono" value={output} onChange={(e) => setOutput(e.target.value)} disabled={running} placeholder="/path/to/params.h5" />
          <label className="small"><input type="checkbox" checked={overwrite} disabled={running} onChange={(e) => setOverwrite(e.target.checked)} /> Overwrite if it exists</label>
          <div className="plot-field-actions">
            <button className="primary" disabled={running || !ticked.size || !output.trim()} onClick={start}>Extract {ticked.size || ""} parameter{ticked.size === 1 ? "" : "s"}</button>
          </div>
          {job && (
            <div style={{ marginTop: 10 }}>
              <progress value={percent} max={100} style={{ width: "100%" }} />
              <div className="small muted">{job.state === "running" ? `${percent}% · ${job.phase}` : job.state === "done" ? `Done: ${count(job.total)} parameters written to` : "Failed"}
                {job.state === "done" && <> <code>{job.output}</code> ({bytes(job.size ?? 0)}) · <a href={url(`extract/${job.id}/download`)} download>Download</a></>}</div>
              {job.error && <div className="banner error">{job.error}</div>}
            </div>
          )}
        </div>
      </section>
      {picker && <PathPicker title="Pick the input (files or folders)" pick="any" roots={roots}
        start={roots.find((r) => r.kind === "data")?.path || null}
        onPick={(p) => setSources((s) => [...s, ...p.filter((x) => !s.includes(x))])} onClose={() => setPicker(false)} />}
    </div>
  );
}
