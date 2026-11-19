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

// The Plot page of `mxp webui` (#/plot): the data-plot notebook in the browser. Pick data files or folders
// (a folder is one data source, read as a whole by FolderParamMgr)
// (every format of the data loaders), find their parameters by name or regex, plot them in 2D (against time)
// or 3D (X, Y, Z), style each trace on its own, then export the figure (PNG, interactive HTML, or the CSV
// of what the view shows). The figure is made by the plotly plot backend of testsuit (routes /plot,
// testsuit.exploit.editor.plots) and drawn here by plotly.js; the page is kept in the browser's storage.
import { useEffect, useMemo, useRef, useState } from "react";
import { get, post, url } from "../api";
import { PathList, PathPicker, type Info, type Root } from "../exploit/ExploitPage";
import { classes, MenuButton, notify } from "../util";

const BASE = "plot";
const STORE = "mxp.plot";

// plotly's default colors: a new trace takes the first one no trace uses
const PALETTE = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"];
const LINES: [string, string][] = [["solid", "Solid"], ["dashed", "Dashed"], ["dotted", "Dotted"], ["dashdot", "Dash-dot"], ["", "No line"]];
const MARKERS: [string, string][] = [[",", "None"], [".", "Dot"], ["o", "Circle"], ["x", "Cross"], ["d", "Diamond"], ["*", "Star"], ["2", "Triangle"], ["_", "Horizontal bar"], ["|", "Vertical bar"]];
const DRAWS: [string, string][] = [["default", "Straight"], ["steps-post", "Steps (post)"], ["steps-pre", "Steps (pre)"], ["steps-mid", "Steps (mid)"]];
const FILLS: [string, string][] = [["none", "None"], ["solid", "Solid"], ["gradient", "Gradient"]];
// plotly.js config: drag pans (the wheel zooms), and nothing may leave the page: plotly.js 3 shows a
// "Share to Plotly Cloud" button by default, uploading the figure (plotlyBackend.PRIVATE_CONFIG)
const CONFIG = { responsive: true, scrollZoom: true, displaylogo: false, showSendToCloud: false,
  plotlyServerURL: "", modeBarButtonsToRemove: ["toImage"] };
const POINTS: [number, string][] = [[20000, "20k"], [100000, "100k"], [500000, "500k"], [2000000, "2M"]];
const MAX_LISTED = 400;

interface Trace {
  id: string; fields: string[]; label: string; color: string; linestyle: string; marker: string; drawstyle: string;
  offset: string; hidden: boolean; width?: string; size?: string; fill?: string;
}
interface Options {
  projection: "2d" | "3d"; title: string; subtitle: string; xLabel: string; yLabel: string; zLabel: string;
  legend: boolean; xTimestamp: boolean; index: string; maxPoints: number;
}
interface State { sources: string[]; traces: Trace[]; options: Options }
interface FileInfo { name: string; path: string; type: string; entries: number | null; info: string[]; error: string | null }
interface SourceInfo { label: string; path: string; folder: boolean; files: FileInfo[]; fields: number }
interface Sources { sources: SourceInfo[]; fields: string[]; total: number; truncated: boolean }
interface TraceReport { id: string; points: number; shown: number; error: string | null; series: number[] }

const OPTIONS: Options = { projection: "2d", title: "", subtitle: "", xLabel: "", yLabel: "", zLabel: "", legend: true,
  xTimestamp: true, index: "", maxPoints: 100000 };

function load(): State {
  try {
    const s = JSON.parse(localStorage.getItem(STORE) || "null");
    if (s && Array.isArray(s.sources) && Array.isArray(s.traces)) return { sources: s.sources, traces: s.traces, options: { ...OPTIONS, ...s.options } };
  } catch { /* none kept */ }
  return { sources: [], traces: [], options: OPTIONS };
}

// ── plotly.js, served by mxp webui from the plotly package ─────────────

let plotlyLoading: Promise<any> | null = null;
function plotly(): Promise<any> {
  const w = window as any;
  if (w.Plotly) return Promise.resolve(w.Plotly);
  plotlyLoading ||= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = url(`${BASE}/plotly.js`);
    s.onload = () => resolve(w.Plotly);
    s.onerror = () => { plotlyLoading = null; reject(new Error("plotly.js could not be loaded")); };
    document.head.appendChild(s);
  });
  return plotlyLoading;
}

/** The figure in the colors of the page (light or dark), filling its box. */
function themed(fig: any, uirevision: string): any {
  const css = getComputedStyle(document.documentElement);
  const v = (n: string) => css.getPropertyValue(n).trim();
  const text = v("--text"), grid = v("--border"), panel = v("--panel");
  const axis = { gridcolor: grid, zerolinecolor: grid, linecolor: grid, color: text };
  const layout = { ...fig.layout, autosize: true, uirevision, paper_bgcolor: panel, plot_bgcolor: panel, dragmode: "pan",
    font: { ...(fig.layout?.font || {}), color: text }, legend: { ...(fig.layout?.legend || {}), bgcolor: panel + "cc", font: { color: text } },
    xaxis: { ...(fig.layout?.xaxis || {}), ...axis }, yaxis: { ...(fig.layout?.yaxis || {}), ...axis },
    margin: { l: 70, r: 20, t: fig.layout?.title?.text ? 80 : 30, b: 60 } };
  if (layout.scene) layout.scene = { ...layout.scene, bgcolor: panel, xaxis: { ...layout.scene.xaxis, ...axis, backgroundcolor: panel },
    yaxis: { ...layout.scene.yaxis, ...axis, backgroundcolor: panel }, zaxis: { ...layout.scene.zaxis, ...axis, backgroundcolor: panel } };
  delete layout.width; delete layout.height;
  return { data: fig.data, layout };
}

/** The value of a line of the figure (its calcdata: points {x, y}, x in order) at x: interpolated, or held
 * as the line draws it in steps; null outside the line. */
function valueAt(points: any[] | undefined, x: number, drawstyle: string): number | null {
  if (!points?.length || x < points[0].x || x > points[points.length - 1].x) return null;
  let lo = 0, hi = points.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (points[mid].x <= x) lo = mid; else hi = mid; }
  const p = points[lo], q = points[hi];
  if (!Number.isFinite(p.y) || !Number.isFinite(q.y)) return null;
  if (q.x === p.x || drawstyle === "steps-post") return x >= q.x ? q.y : p.y;
  if (drawstyle === "steps-pre") return x <= p.x ? p.y : q.y;
  if (drawstyle === "steps-mid") return x < (p.x + q.x) / 2 ? p.y : q.y;
  return p.y + ((q.y - p.y) * (x - p.x)) / (q.x - p.x);
}

const fmt = (v: number) => (Math.abs(v) >= 1e5 || (Math.abs(v) < 1e-3 && v !== 0) ? v.toExponential(3) : String(Math.round(v * 1e4) / 1e4));

/** Seconds of a date of plotly's date axis ("2026-11-18 09:30:12.5", UTC), or the number itself. */
function seconds(v: any): number {
  if (typeof v === "number") return v;
  return Date.parse(String(v).replace(" ", "T") + "Z") / 1000;
}

/** The fields matching a filter: words anywhere (case ignored), or regexes as in the notebook, "file::param". */
function matcher(filter: string): (field: string) => boolean {
  const f = filter.trim();
  if (!f) return () => true;
  const re = (s: string) => { try { return new RegExp(s, "i"); } catch { return null; } };
  if (f.includes("::")) {
    const [file, param] = f.split("::", 2);
    const rf = re(file), rp = re(param);
    if (rf && rp) return (x) => { const [a, b] = x.includes("::") ? x.split("::", 2) : ["", x]; return rf.test(a) && rp.test(b); };
  }
  const r = re(f);
  if (r) return (x) => r.test(x);
  const low = f.toLowerCase();
  return (x) => x.toLowerCase().includes(low);
}

const fileOf = (f: string) => (f.includes("::") ? f.split("::")[0] : "");
const paramOf = (f: string) => (f.includes("::") ? f.slice(f.indexOf("::") + 2) : f);
const count = (n: number) => n.toLocaleString("en-US");

async function download(path: string, body: unknown, name: string) {
  const r = await fetch(url(path), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch { /* text */ }
    throw new Error(detail);
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await r.blob());
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ── the page ───────────────────────────────────────────────────────────

export function PlotPage() {
  const [state, setState] = useState<State>(load);
  const { sources, traces, options } = state;
  const [info, setInfo] = useState<Info | null>(null);
  const [found, setFound] = useState<Sources | null>(null);
  const [opening, setOpening] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [picker, setPicker] = useState(false);
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState<string[]>([]);          // in the order they were ticked: X, Y, Z
  const [open, setOpen] = useState<string | null>(null);       // the trace whose style is shown
  const [reports, setReports] = useState<Record<string, TraceReport>>({});
  const [drawing, setDrawing] = useState(false);
  const [figError, setFigError] = useState<string | null>(null);
  const [empty, setEmpty] = useState(true);
  const plotBox = useRef<HTMLDivElement>(null);
  const view = useRef<{ x?: number[]; y?: number[] }>({});
  const roots: Root[] = info?.roots || [];

  useEffect(() => { get<Info>("exploit").then(setInfo).catch(() => setInfo(null)); }, []);
  useEffect(() => { try { localStorage.setItem(STORE, JSON.stringify(state)); } catch { /* full */ } }, [state]);

  const setOptions = (o: Partial<Options>) => setState((s) => ({ ...s, options: { ...s.options, ...o } }));
  const setTraces = (f: (t: Trace[]) => Trace[]) => setState((s) => ({ ...s, traces: f(s.traces) }));
  const editTrace = (id: string, t: Partial<Trace>) => setTraces((all) => all.map((x) => (x.id === id ? { ...x, ...t } : x)));

  // the fields of the sources
  useEffect(() => {
    setFound(null); setSourceError(null);
    if (!sources.length) return;
    setOpening(true);
    post<Sources>(`${BASE}/sources`, { paths: sources })
      .then((s) => { setFound(s); setPicked((p) => p.filter((f) => s.fields.includes(f))); })
      .catch((e) => setSourceError(e.message))
      .finally(() => setOpening(false));
  }, [sources]);

  const match = useMemo(() => matcher(filter), [filter]);
  const matching = useMemo(() => (found?.fields || []).filter((f) => f !== options.index && match(f)), [found, match, options.index]);
  const byFile = useMemo(() => {
    const g: [string, string[]][] = [];
    for (const f of matching.slice(0, MAX_LISTED)) {
      const file = fileOf(f);
      const last = g[g.length - 1];
      if (last && last[0] === file) last[1].push(f); else g.push([file, [f]]);
    }
    return g;
  }, [matching]);
  const plotted = useMemo(() => new Set(traces.flatMap((t) => t.fields)), [traces]);

  const newTrace = (fields: string[], used: string[]): Trace => {
    const color = PALETTE.find((c) => !used.includes(c)) || PALETTE[used.length % PALETTE.length];
    used.push(color);
    return { id: Math.random().toString(36).slice(2, 10), fields, label: "", color, linestyle: "solid",
      marker: ",", drawstyle: "default", offset: "", hidden: false };
  };
  const add = (fields: string[]) => {
    if (!fields.length) return;
    const used = traces.map((t) => t.color);
    const added = options.projection === "3d" && fields.length === 3 ? [newTrace(fields, used)] : fields.map((f) => newTrace([f], used));
    setTraces((t) => [...t, ...added]);
    setPicked([]);
  };
  const toggle = (f: string) => setPicked((p) => (p.includes(f) ? p.filter((x) => x !== f) : [...p, f]));

  // the figure, made again when what it shows changes (not on a new label being typed: debounced)
  const spec = useMemo(() => ({ sources, ...options, index: options.index || null, traces }), [sources, options, traces]);
  const specText = JSON.stringify(spec);
  const uirevision = `${sources.join("|")}#${options.projection}`;
  useEffect(() => {
    const visible = traces.filter((t) => !t.hidden);
    if (!sources.length || !visible.length) {
      setEmpty(true); setFigError(null); setReports({});
      if (plotBox.current) plotly().then((P) => P.purge(plotBox.current)).catch(() => undefined);
      return;
    }
    let stale = false;
    const timer = setTimeout(async () => {
      setDrawing(true);
      try {
        const [P, answer] = await Promise.all([plotly(), post<{ figure: any; traces: TraceReport[] }>(`${BASE}/figure`, JSON.parse(specText))]);
        if (stale) return;
        setReports(Object.fromEntries(answer.traces.map((r) => [r.id, r])));
        if (!answer.figure) { setEmpty(true); setFigError("No trace could be drawn: see the traces."); P.purge(plotBox.current); return; }
        const { data, layout } = themed(answer.figure, uirevision);
        await P.react(plotBox.current, data, layout, CONFIG);
        setEmpty(false); setFigError(null);
      } catch (e: any) {
        if (!stale) setFigError(e.message || String(e));
      } finally {
        if (!stale) setDrawing(false);
      }
    }, 250);
    return () => { stale = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [specText]);

  // the view (zoom) of a 2D figure: the CSV export keeps to it
  useEffect(() => {
    const el = plotBox.current as any;
    if (!el || empty) return;
    const on = (e: any) => {
      if (e["xaxis.autorange"]) view.current.x = undefined;
      if (e["yaxis.autorange"]) view.current.y = undefined;
      if (e["xaxis.range[0]"] !== undefined) view.current.x = [seconds(e["xaxis.range[0]"]), seconds(e["xaxis.range[1]"])];
      if (e["yaxis.range[0]"] !== undefined) view.current.y = [e["yaxis.range[0]"], e["yaxis.range[1]"]];
    };
    el.on?.("plotly_relayout", on);
    return () => el.removeListener?.("plotly_relayout", on);
  }, [empty]);
  useEffect(() => { view.current = {}; }, [uirevision]);


  const fileName = (options.title || traces.find((t) => !t.hidden)?.fields.map(paramOf).join("_") || "plot").replace(/[^\w.-]+/g, "_").slice(0, 80);
  const exportPng = async () => {
    const P = await plotly();
    await P.downloadImage(plotBox.current, { format: "png", filename: fileName, width: 1600, height: 900, scale: 1 });
  };
  const exportFile = async (kind: "html" | "csv") => {
    try {
      await download(`${BASE}/export/${kind}`, { ...spec, view: kind === "csv" ? view.current : undefined }, `${fileName}.${kind}`);
    } catch (e: any) { notify(e.message, "error"); }
  };

  const addSources = (p: string[]) => setState((s) => ({ ...s, sources: [...s.sources, ...p.filter((x) => !s.sources.includes(x))] }));
  const removeSource = (p: string) => setState((s) => ({ ...s, sources: s.sources.filter((x) => x !== p) }));
  const clear = () => { setTraces(() => []); setOpen(null); };
  const visibleCount = traces.filter((t) => !t.hidden).length;
  const is3d = options.projection === "3d";

  // the delta between two traces (2D): a vertical segment between them at the mouse, with their difference
  const [delta, setDelta] = useState<string[]>([]);
  const toggleDelta = (id: string) => setDelta((d) => (d.includes(id) ? d.filter((x) => x !== id) : [...d, id].slice(-2)));
  useEffect(() => {
    const gd = plotBox.current as any;
    const pair = delta.filter((id) => traces.some((t) => t.id === id && !t.hidden) && reports[id]?.series?.length);
    if (!gd || empty || is3d || pair.length !== 2) return;
    const [a, b] = pair.map((id) => ({ trace: traces.find((t) => t.id === id)!, index: reports[id].series[0] }));
    let frame = 0;
    const css = getComputedStyle(document.documentElement);
    const text = css.getPropertyValue("--text").trim(), panel = css.getPropertyValue("--panel").trim();
    const clear = () => {
      cancelAnimationFrame(frame);
      if (gd._fullLayout?.shapes?.length || gd._fullLayout?.annotations?.some((n: any) => n.name === "delta"))
        plotly().then((P) => P.relayout(gd, { shapes: [], annotations: [] })).catch(() => undefined);
    };
    const move = (e: MouseEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const xa = gd._fullLayout?.xaxis, area = gd.querySelector(".nsewdrag")?.getBoundingClientRect();
        if (!xa || !area || e.clientX < area.left || e.clientX > area.right || e.clientY < area.top || e.clientY > area.bottom) return clear();
        const x = xa.p2c(e.clientX - area.left);
        const ya = valueAt(gd.calcdata?.[a.index], x, a.trace.drawstyle), yb = valueAt(gd.calcdata?.[b.index], x, b.trace.drawstyle);
        if (ya === null || yb === null) return clear();
        const name = (t: Trace) => t.label || t.fields.map(paramOf).join(" / ");
        plotly().then((P) => P.relayout(gd, {
          shapes: [{ type: "line", xref: "x", yref: "y", x0: x, x1: x, y0: ya, y1: yb, line: { color: text, width: 2, dash: "dot" } }],
          annotations: [{ name: "delta", xref: "x", yref: "y", x, y: (ya + yb) / 2, xanchor: "left", xshift: 8, showarrow: false,
            align: "left", bgcolor: panel, bordercolor: text, borderwidth: 1, borderpad: 4, font: { color: text, size: 12 },
            text: `${name(b.trace)} − ${name(a.trace)}<br><b>Δ = ${fmt(yb - ya)}</b>` }],
        })).catch(() => undefined);
      });
    };
    gd.addEventListener("mousemove", move);
    gd.addEventListener("mouseleave", clear);
    return () => { gd.removeEventListener("mousemove", move); gd.removeEventListener("mouseleave", clear); clear(); };
  }, [delta, reports, empty, is3d, traces]);
  const addLabel = is3d && picked.length === 3 ? "Plot X, Y, Z" : `Plot ${picked.length || ""}`;

  return (
    <div className="plot-page">
      <aside className="plot-side">
        <section className="card plot-section">
          <div className="card-head"><strong>1. Data</strong><span className="muted small">files or folders, any format</span>
            <span style={{ flex: 1 }} /><button className="small" onClick={() => setPicker(true)}>Browse…</button></div>
          <PathList paths={sources} roots={roots} onRemove={removeSource} empty="Pick recordings: TDMS, MDF, CSV, HDF5, DXD… or a folder of them, read as one source (a recording split in several files is merged in time)." />
          {opening && <p className="muted small">Opening…</p>}
          {sourceError && <div className="banner error">{sourceError}</div>}
          {found?.sources.map((src) => (
            // a folder is one data source: a parameter of several of its files is one field, merged in time
            <details key={src.path} className="small plot-files" title={src.path}>
              <summary><span className="mono">{src.label}</span>{" "}
                <span className="muted">{src.folder ? `folder of ${src.files.length} file${src.files.length > 1 ? "s" : ""}` : src.files[0]?.type}, {count(src.fields)} fields</span>
                {src.files.some((f) => f.error) && <span className="badge bad">{src.files.filter((f) => f.error).length} unreadable</span>}</summary>
              <table className="plot-files-table"><tbody>
                {src.files.map((f) => (
                  <tr key={f.path} title={f.path}><td className="mono">{f.name}</td><td><span className="badge">{f.type}</span></td>
                    <td className={f.error ? "plot-file-error" : "muted"}>{f.error || [...(f.entries != null && f.entries > 1 ? [`${count(f.entries)} rows`] : []), ...f.info].join(", ")}</td></tr>
                ))}
              </tbody></table>
            </details>
          ))}
        </section>

        <section className="card plot-section plot-fields">
          <div className="card-head"><strong>2. Fields</strong>
            <span className="muted small">{found ? `${count(matching.length)} of ${count(found.total)}` : "of the data"}</span></div>
          <input className="mono" value={filter} onChange={(e) => setFilter(e.target.value)} disabled={!found}
            placeholder="filter: words or regex, source::param" title="Words found anywhere in the name (case ignored), or a regex. 'source::param' matches the source (file or folder) and the parameter apart, as in the notebook. Double-click a field to plot it." />
          <div className="plot-field-list">
            {!found && <p className="muted small">Pick data first.</p>}
            {found && !matching.length && <p className="muted small">No field matches.</p>}
            {byFile.map(([file, fields]) => (
              <div key={file}>
                {(found?.sources.length || 0) > 1 && <div className="plot-file-head muted small">{file}</div>}
                {fields.map((f) => (
                  <label key={f} className={classes("plot-field", picked.includes(f) && "selected")} title={f} onDoubleClick={(e) => { e.preventDefault(); add([f]); }}>
                    <input type="checkbox" checked={picked.includes(f)} onChange={() => toggle(f)} />
                    <span className="mono">{paramOf(f)}</span>
                    {is3d && picked.includes(f) && picked.length <= 3 && <span className="badge info">{"XYZ"[picked.indexOf(f)]}</span>}
                    {plotted.has(f) && <span className="plot-dot" title="plotted" style={{ background: traces.find((t) => t.fields.includes(f))?.color }} />}
                  </label>
                ))}
              </div>
            ))}
            {matching.length > MAX_LISTED && <p className="muted small">{count(matching.length - MAX_LISTED)} more: refine the filter.</p>}
          </div>
          <div className="plot-field-actions">
            <button className="primary" disabled={!picked.length} onClick={() => add(picked)}
              title={is3d ? "3 fields ticked: one 3D trace, X, Y and Z in the order ticked. Otherwise each field must have 3 columns." : "One trace per field"}>{addLabel}</button>
            <button className="small" disabled={!matching.length || matching.length > 50} onClick={() => setPicked(matching.slice(0, 50))} title="Tick every field listed (50 at most)">Tick all</button>
            {picked.length > 0 && <button className="small" onClick={() => setPicked([])}>Untick</button>}
            <span style={{ flex: 1 }} />
            {found && !is3d && (
              <select className="small" value={options.index} onChange={(e) => setOptions({ index: e.target.value })}
                title="The time of the values: as the format gives it (automatic), or another field (the notebook's 'Use First Line for Timestamps')">
                <option value="">time: automatic</option>
                {found.fields.map((f) => <option key={f} value={f}>time: {paramOf(f)}</option>)}
              </select>
            )}
          </div>
        </section>

        <section className="card plot-section">
          <div className="card-head"><strong>3. Traces</strong><span className="muted small">{traces.length ? `${visibleCount} shown` : ""}</span>
            <span style={{ flex: 1 }} />{traces.length > 0 && <button className="small" onClick={clear}>Clear</button>}</div>
          {!traces.length && <p className="muted small">Tick fields and plot them, or double-click one.</p>}
          {traces.map((t) => {
            const r = reports[t.id];
            return (
              <div key={t.id} className={classes("plot-trace", t.hidden && "hidden", open === t.id && "open")}>
                <div className="plot-trace-row">
                  <input type="color" value={t.color} onChange={(e) => editTrace(t.id, { color: e.target.value })} title="Color" />
                  <button className="link plot-trace-name" onClick={() => setOpen(open === t.id ? null : t.id)} title={`${t.fields.join("\n")}\nClick for its style`}>
                    {t.label || t.fields.map(paramOf).join(" / ")}
                  </button>
                  {r?.error ? <span className="badge bad" title={r.error}>error</span>
                    : r && r.shown < r.points ? <span className="muted small" title={`${count(r.points)} points, ${count(r.shown)} drawn (min and max kept)`}>{count(r.shown)}/{count(r.points)}</span>
                    : r ? <span className="muted small">{count(r.points)} pts</span> : null}
                  {!is3d && <button className={classes("icon", "plot-delta", delta.includes(t.id) && "on")} onClick={() => toggleDelta(t.id)}
                    title="Delta: pick two traces, then move the mouse over the figure to see the difference between them">Δ</button>}
                  <button className="icon" title={t.hidden ? "Show" : "Hide"} onClick={() => editTrace(t.id, { hidden: !t.hidden })}>{t.hidden ? "◌" : "●"}</button>
                  <button className="icon" title="Remove" onClick={() => setTraces((all) => all.filter((x) => x.id !== t.id))}>×</button>
                </div>
                {r?.error && <div className="small plot-trace-error">{r.error}</div>}
                {open === t.id && (
                  <div className="plot-trace-style">
                    <label>Label<input value={t.label} placeholder={t.fields.map(paramOf).join(" / ")} onChange={(e) => editTrace(t.id, { label: e.target.value })} /></label>
                    <label>Line<select value={t.linestyle} onChange={(e) => editTrace(t.id, { linestyle: e.target.value })}>{LINES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
                    <label>Markers<select value={t.marker} onChange={(e) => editTrace(t.id, { marker: e.target.value })}>{MARKERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
                    {!is3d && <label>Drawn<select value={t.drawstyle} onChange={(e) => editTrace(t.id, { drawstyle: e.target.value })}>{DRAWS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>}
                    <label>Line width<input type="number" min={0} max={20} step={0.5} value={t.width ?? ""} placeholder="2" onChange={(e) => editTrace(t.id, { width: e.target.value })} /></label>
                    <label>Dot size<input type="number" min={1} max={40} step={1} value={t.size ?? ""} placeholder={is3d ? "3" : "6"} onChange={(e) => editTrace(t.id, { size: e.target.value })} /></label>
                    {!is3d && <label title="Fill between the line and zero: plain, or fading from the line down">Fill<select value={t.fill || "none"} onChange={(e) => editTrace(t.id, { fill: e.target.value })}>{FILLS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>}
                    <label title="Added to the values: a number or expression ('-12', '1e3/2'), or 'autoz:XXs' to start from zero (minus the mean of the first XX seconds)">Offset
                      <input className="mono" value={t.offset} placeholder="0, -12, autoz:2s" onChange={(e) => editTrace(t.id, { offset: e.target.value })} /></label>
                    <div className="muted small mono plot-trace-fields">{t.fields.join("\n")}</div>
                  </div>
                )}
              </div>
            );
          })}
        </section>
      </aside>

      <section className="plot-main">
        <div className="plot-toolbar">
          <div className="segmented">
            <button className={classes(!is3d && "selected")} onClick={() => setOptions({ projection: "2d" })} title="Values against time">2D</button>
            <button className={classes(is3d && "selected")} onClick={() => setOptions({ projection: "3d" })} title="Trajectories: X, Y, Z">3D</button>
          </div>
          <input className="plot-title" value={options.title} onChange={(e) => setOptions({ title: e.target.value })} placeholder="Figure title" />
          <input className="plot-subtitle" value={options.subtitle} onChange={(e) => setOptions({ subtitle: e.target.value })} placeholder="subtitle" />
          <span style={{ flex: 1 }} />
          {drawing && <span className="muted small">drawing…</span>}
          <MenuButton label="Export" disabled={empty} items={[
            { label: "PNG image", title: "The figure as it is shown, 1600×900", onClick: exportPng },
            { label: "Interactive HTML", title: "A page of its own (works offline): zoom, hover, rotate", onClick: () => exportFile("html") },
            ...(!is3d ? [{ label: "CSV of the view", title: "Every point inside the current zoom, all traces on one time column", onClick: () => exportFile("csv") }] : []),
          ]} />
        </div>
        <div className="plot-toolbar plot-axes">
          <label>X<input value={options.xLabel} onChange={(e) => setOptions({ xLabel: e.target.value })} placeholder={is3d ? "X" : "time range (automatic)"} /></label>
          <label>Y<input value={options.yLabel} onChange={(e) => setOptions({ yLabel: e.target.value })} placeholder={is3d ? "Y" : "Values"} /></label>
          {is3d && <label>Z<input value={options.zLabel} onChange={(e) => setOptions({ zLabel: e.target.value })} placeholder="Z" /></label>}
          <label className="check"><input type="checkbox" checked={options.legend} onChange={(e) => setOptions({ legend: e.target.checked })} />Legend</label>
          {!is3d && <label className="check" title="The x values are dates (seconds since 1970): shown as times of day"><input type="checkbox" checked={options.xTimestamp} onChange={(e) => setOptions({ xTimestamp: e.target.checked })} />Time axis</label>}
          <label title="Points drawn per trace at most: beyond, the min and max of each slice are kept, so peaks stay. Exports to CSV take every point.">Points
            <select value={options.maxPoints} onChange={(e) => setOptions({ maxPoints: Number(e.target.value) })}>
              {POINTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select></label>
        </div>
        {figError && <div className="banner error">{figError}</div>}
        <div className="plot-figure">
          <div ref={plotBox} className="plot-canvas" />
          {empty && !figError && (
            <div className="plot-empty muted">
              {!sources.length ? "1. Pick data files or folders." : !traces.length ? "2. Tick fields, then Plot (or double-click a field)." : "Every trace is hidden."}
              <div className="small">{is3d ? "Drag to rotate, scroll to zoom" : "Drag to pan, scroll to zoom on a point, double-click to reset; Δ on two traces measures them"}.</div>
            </div>
          )}
        </div>
      </section>

      {picker && <PathPicker title="Add data (files or folders)" pick="any" roots={roots}
        start={sources[0] ? sources[0].replace(/\/[^/]*$/, "") : roots.find((r) => r.kind === "data")?.path || null}
        onPick={addSources} onClose={() => setPicker(false)} />}
    </div>
  );
}
