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

// File tree with viewers for text, YAML, CSV, logs and images, plus downloads (sequencer README, section 13.1).
import { useEffect, useMemo, useRef, useState } from "react";
import { parse } from "yaml";
import { get, post, url } from "./api";
import { CodeEditor, languageOf, type ValueHover } from "./monaco";
import { classes, openInNewTab, size } from "./util";
import { CONFIG, PALETTE, plotly, themed, TIME } from "./plot/PlotPage";

export interface Entry {
  name: string;
  path: string;
  dir: boolean;
  size: number | null;
  /** a file its results zip leaves out (large_files.mode: external): next to the zip, in <name>_results.data/ */
  external?: boolean;
}

const TEXT = /\.(ya?ml|json|txt|log|csv|tsv|md|ini|cfg|conf|sh|py|xml|html?|j2|toml|propj)$/i;
const IMAGE = /\.(png|jpe?g|gif|svg|webp)$/i;

/** What a file of a datapack was rendered from: the keys it used (key -> dictionary), and where each value is. */
export interface RenderedFile {
  keys: Record<string, string>;
  values?: ValueHover[];
}

async function fetchText(href: string): Promise<string | null> {
  try {
    const r = await fetch(href);
    return r.ok ? await r.text() : null;
  } catch {
    return null;
  }
}

/**
 * The rendered files of the datapack at the root of a folder API, by path: keys from datapack.yml (every
 * datapack), values from values.json (datapacks generated since it exists).
 */
async function renderedFiles(base: string, q: string): Promise<Record<string, RenderedFile>> {
  const [pack, values] = await Promise.all([fetchText(url(`${base}/datapack.yml${q}`)), fetchText(url(`${base}/values.json${q}`))]);
  const files: Record<string, RenderedFile> = {};
  try {
    const slots = (pack && parse(pack)?.slots) || {};
    for (const [slot, def] of Object.entries<any>(slots)) {
      for (const input of def?.inputs || []) {
        for (const [name, keys] of Object.entries<any>(input?.keys || {})) files[`data/${slot}/input/${name}`] = { keys: keys || {} };
      }
    }
  } catch {
    // not a datapack, or a datapack.yml this viewer does not read: no keys
  }
  try {
    const found = values ? JSON.parse(values)?.files || {} : {};
    for (const [path, v] of Object.entries<any>(found)) {
      const file = (files[path] ||= { keys: {} });
      const expressions: string[] = v.expressions || [];
      file.values = (v.values || []).map(([start, end, expression, keys]: [number, number, number, string[]]) => ({
        start, end,
        hover: [
          "```jinja\n{{ " + (expressions[expression] ?? "?") + " }}\n```",
          ...(keys.length ? keys.map((k) => `- \`${k}\`` + (file.keys[k] ? ` from ${file.keys[k]}` : "")) : ["no key read here (a loop or set variable)"]),
        ].join("\n"),
      }));
    }
  } catch {
    // an unreadable values.json: keys only
  }
  return files;
}

/** Browses a folder API: base + "/" + path lists a folder (JSON array) or downloads a file. With plot, plot +
 * "/" + path says whether the Plot page can open the file (a data loader reads it): it then gets a link there. */
export function FileBrowser({ base, query, initial, line, plot }: { base: string; query?: string; initial?: string; line?: number | null; plot?: string }) {
  const [open, setOpen] = useState<Record<string, Entry[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set([""]));
  const [selected, setSelected] = useState<Entry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rendered, setRendered] = useState<Record<string, RenderedFile>>({});
  const q = query ? `?${query}` : "";
  const load = async (path: string) => {
    try {
      const list = await get<Entry[]>(`${base}/${path}${q}`);
      setOpen((o) => ({ ...o, [path]: Array.isArray(list) ? list : [] }));
    } catch (e: any) {
      setError(e.message);
    }
  };
  useEffect(() => {
    setOpen({});
    setExpanded(new Set([""]));
    setSelected(null);
    setError(null);
    setRendered({});
    load("");
    let current = true;
    renderedFiles(base, q).then((r) => current && setRendered(r));
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, query]);
  useEffect(() => {
    if (!initial) return;
    setSelected({ name: initial.split("/").pop()!, path: initial, dir: false, size: null });
    // its folders opened in the tree
    const parts = initial.split("/").slice(0, -1);
    const folders = parts.map((_, i) => parts.slice(0, i + 1).join("/"));
    if (folders.length) {
      setExpanded((e) => new Set([...e, ...folders]));
      folders.forEach((f) => load(f));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial, base]);
  const toggle = (e: Entry) => {
    if (!e.dir) return setSelected(e);
    const next = new Set(expanded);
    if (next.has(e.path)) next.delete(e.path);
    else {
      next.add(e.path);
      if (!open[e.path]) load(e.path);
    }
    setExpanded(next);
  };
  const render = (path: string, depth: number): any =>
    (open[path] || []).map((e) => (
      <div key={e.path}>
        <div className={classes("tree-row", selected?.path === e.path && "selected")} style={{ paddingLeft: 8 + depth * 14 }}
          onClick={() => toggle(e)}>
          <span className="tree-icon">{e.dir ? (expanded.has(e.path) ? "▾" : "▸") : "·"}</span>
          {e.name}
          {!e.dir && <span className="muted small"> {size(e.size)}</span>}
          {e.external && <> <span className="chip small" title="Not in the results zip (large_files.mode: external): it is kept next to it, in <datapack>_results.data/, and linked here">🔗 external</span></>}
        </div>
        {e.dir && expanded.has(e.path) && render(e.path, depth + 1)}
      </div>
    ));
  return (
    <div className="file-browser">
      <div className="tree">
        {error && <div className="error-box">{error}</div>}
        {render("", 0)}
      </div>
      <div className="viewer">
        {selected ? <FileView href={url(`${base}/${selected.path}${q}`)} path={selected.path} rendered={rendered[selected.path]}
          plot={plot && `${plot}/${selected.path}`} line={selected.path === initial ? line : null} /> : <p className="muted">Pick a file to see it.</p>}
      </div>
    </div>
  );
}

/** The file on this host and its data format, when the Plot page can open it (GET plot: 404 when not). */
interface Readable { path: string; format: string; fields: number | null; label: string; names: string[] | null; size: number }

// a data file plotted where it is shown: 3 fields or fewer (its times left out), of 2 to 100k values
const INLINE_FIELDS = 3;
const INLINE_MIN = 2;
const INLINE_MAX = 100_000;
const INLINE_BYTES = 20 << 20;      // a bigger file holds more values: not loaded to find out

/** Why a data file is not plotted where it is shown, known before loading it; null when it may be. */
function quickPlotRefused(file: Readable): string | null {
  if (file.fields == null) return "its loader takes too long to list its fields";
  const fields = file.names?.filter((f) => !TIME.test(f));
  if (!fields || fields.length > INLINE_FIELDS)
    return `${fields?.length ?? file.fields} fields besides its times, more than ${INLINE_FIELDS}`;
  if (!fields.length) return "no field besides its times";
  if (file.size > INLINE_BYTES) return `${size(file.size)}, too big to be loaded for a quick look`;
  return null;
}

/** The fields of a small data file, plotted (2D, against time) by the Plot page's backend; nothing when it has
 *  too few or too many values. */
function InlinePlot({ file, onSkip }: { file: Readable; onSkip: (why: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);
  const fields = (file.names || []).filter((f) => !TIME.test(f));
  const key = `${file.path}|${fields.join(",")}`;
  useEffect(() => {
    setShown(false);
    let current = true;
    const spec = { sources: [file.path], projection: "2d", legend: fields.length > 1, xTimestamp: true, maxPoints: INLINE_MAX,
      traces: fields.map((f, i) => ({ id: f, fields: [`${file.label}::${f}`], label: "", color: PALETTE[i % PALETTE.length],
        linestyle: "solid", marker: ",", drawstyle: "default", offset: "", hidden: false })) };
    Promise.all([plotly(), post<{ figure: any; traces: { points: number; error: string | null }[] }>("plot/figure", spec)])
      .then(async ([P, answer]) => {
        if (!current || !box.current) return;
        const points = Math.max(0, ...answer.traces.map((t) => t.points || 0));
        const failed = answer.traces.find((t) => t.error)?.error;
        if (points > INLINE_MAX) return onSkip(`${points.toLocaleString("en-US")} values, more than ${INLINE_MAX.toLocaleString("en-US")}`);
        if (!answer.figure || points < INLINE_MIN) return onSkip(failed || `${points} value${points === 1 ? "" : "s"}, fewer than ${INLINE_MIN}`);
        setShown(true);
        const { data, layout } = themed(answer.figure, key);
        // a quick look: sized to its box, no toolbar (the Plot… button has the rest)
        await P.react(box.current, data, { ...layout, title: undefined, autosize: false, width: box.current.clientWidth, height: 298,
          margin: { l: 50, r: 20, t: 10, b: 40 } }, { ...CONFIG, displayModeBar: false });
      })
      .catch((e) => current && onSkip(e.message || String(e)));
    return () => { current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  // plotly marks the div it draws in with classes of its own: React must not set its className again
  return <div className={classes("file-plot", !shown && "hidden")}><div ref={box} className="file-plot-canvas" /></div>;
}

// what the viewer shows of a file at first, and adds on each "Load more": a part only, the rest is not downloaded
// (a data file can be GBs)
const VIEW_BYTES = 5 << 20;
// what it shows at most (Load whole file included): past this, the editor and the table freeze; Download, or Plot
const VIEW_MAX = 50 << 20;

/** A part of a file: its bytes from start (max at most), whether the file has more after them, its size when
 *  the server tells it. */
interface Part { bytes: Uint8Array; more: boolean; total: number | null }

/** The bytes [skip, skip + want) of a response's body, at most: the download stops there. */
async function readBody(r: Response, skip: number, want: number): Promise<Uint8Array> {
  const out = new Uint8Array(want);
  let at = 0;
  if (!r.body) {
    const all = new Uint8Array(await r.arrayBuffer()).subarray(skip, skip + want);
    out.set(all);
    return out.subarray(0, all.length);
  }
  const reader = r.body.getReader();
  let seen = 0;     // bytes of the body read, the skipped ones included
  while (at < want) {
    const { done, value } = await reader.read();
    if (done) break;
    const from = Math.max(0, skip - seen);
    seen += value.length;
    if (from >= value.length) continue;
    const take = value.subarray(from, from + want - at);
    out.set(take, at);
    at += take.length;
  }
  if (at >= want) reader.cancel().catch(() => { /* the connection is closed anyway */ });
  return out.subarray(0, at);
}

/** The bytes of the file at href from start, max at most: asked as an HTTP range (206), or, from a server that
 *  answers with the whole file (200), read through the body up to them. One byte more is read to know whether
 *  the file goes on. */
async function readPart(href: string, start: number, max: number, signal: AbortSignal): Promise<Part> {
  const r = await fetch(href, { signal, headers: { Range: `bytes=${start}-${start + max}` } });
  if (r.status === 416) return { bytes: new Uint8Array(0), more: false, total: start };
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  const ranged = r.status === 206;
  const totalHeader = ranged ? r.headers.get("Content-Range")?.split("/")[1] : r.headers.get("Content-Length");
  const total = totalHeader && /^\d+$/.test(totalHeader) ? Number(totalHeader) : null;
  const bytes = await readBody(r, ranged ? 0 : start, max + 1);
  return { bytes: bytes.subarray(0, max), more: bytes.length > max, total };
}

/** Where to cut UTF-8 bytes so that no character is split: before the last one when it is incomplete. */
function utf8Cut(bytes: Uint8Array): number {
  let i = bytes.length;
  let back = 0;
  // up to 3 continuation bytes (10xxxxxx) before the lead byte of the last character
  while (i > 0 && back < 4 && (bytes[i - 1] & 0xc0) === 0x80) { i--; back++; }
  if (i === 0) return bytes.length;
  const lead = bytes[i - 1];
  const len = lead >= 0xf0 ? 4 : lead >= 0xe0 ? 3 : lead >= 0xc0 ? 2 : 1;
  return back + 1 >= len ? bytes.length : i - 1;
}

export function FileView({ href, path, rendered, plot, line }: { href: string; path: string; rendered?: RenderedFile; plot?: string | false;
  line?: number | null }) {
  const [text, setText] = useState<string | null>(null);
  // how far the text shown goes in the file (bytes), whether the file has more, its size (when known)
  const [shown, setShown] = useState<{ next: number; more: boolean; total: number | null }>({ next: 0, more: false, total: null });
  const [loading, setLoading] = useState(false);
  const loads = useRef<AbortController | null>(null);
  const [readable, setReadable] = useState<Readable | null>(null);
  const [skipped, setSkipped] = useState<string | null>(null);    // why the quick plot was not drawn, found when trying it
  useEffect(() => {
    setReadable(null);
    setSkipped(null);
    if (!plot) return;
    let current = true;
    get<Readable>(plot).then((r) => current && setReadable(r)).catch(() => { /* no data loader reads it */ });
    return () => { current = false; };
  }, [plot]);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"auto" | "raw">("auto");
  // a file rendered from a template is text, whatever its extension
  const isText = TEXT.test(path) || !!rendered || !!line;      // a line to show: a text file (a script)
  /** Shows the bytes of the file from start, max at most, after the text shown (from 0: instead of it). */
  const load = (start: number, max: number) => {
    loads.current?.abort();
    const aborted = (loads.current = new AbortController());
    setLoading(true);
    readPart(href, start, max, aborted.signal)
      .then(({ bytes, more, total }) => {
        if (aborted.signal.aborted) return;
        // cut at a byte: a character split there is left for the next part
        const end = more ? utf8Cut(bytes) : bytes.length;
        const part = new TextDecoder().decode(bytes.subarray(0, end));
        setText((t) => (start && t !== null ? t + part : part));
        setShown({ next: start + end, more, total: total ?? (more ? null : start + end) });
      })
      .catch((e) => !aborted.signal.aborted && setError(e.message))
      .finally(() => !aborted.signal.aborted && setLoading(false));
  };
  useEffect(() => {
    setText(null);
    setError(null);
    setShown({ next: 0, more: false, total: null });
    if (IMAGE.test(path) || !isText) return;
    load(0, VIEW_BYTES);
    // another file picked meanwhile: the rest of this one is not downloaded
    return () => loads.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [href, path, isText]);
  const room = VIEW_MAX - shown.next;         // what may still be shown
  const loadMore = () => load(shown.next, Math.min(VIEW_BYTES, room));
  const wholeFits = shown.total !== null && shown.total <= VIEW_MAX;
  const loadWhole = () => shown.total !== null && load(shown.next, shown.total - shown.next);
  const keys = Object.entries(rendered?.keys || {});
  const openPlot = () => readable && openInNewTab({ path: "exploit/plot", query: { source: readable.path } });
  const noQuickPlot = readable && (skipped || quickPlotRefused(readable));
  return (
    <div className="file-view">
      <div className="file-view-head">
        <code>{path}</code>
        <span className="spacer" />
        {/\.(csv|tsv)$/i.test(path) && (
          <button className="small" onClick={() => setMode(mode === "auto" ? "raw" : "auto")}>{mode === "auto" ? "raw text" : "table"}</button>
        )}
        {readable && (
          <button className="small" onClick={openPlot}
            title={`Open it in the Plot subtab of the Exploit tab, in a new browser tab (read as ${readable.format}${readable.fields != null ? `, ${readable.fields} field${readable.fields === 1 ? "" : "s"}` : ""})`}>Plot…</button>
        )}
        <a className="button small" href={href} download={path.split("/").pop()}>Download</a>
      </div>
      {error && <div className="error-box">{error}</div>}
      {keys.length > 0 && (
        <details className="file-keys">
          <summary>
            {keys.length} key{keys.length > 1 ? "s" : ""} used to generate this file
            {rendered?.values?.length ? <span className="muted">: hover an underlined value to see its expression and keys</span> : null}
          </summary>
          <table className="grid compact">
            <tbody>{keys.map(([k, origin]) => <tr key={k}><td><code>{k}</code></td><td className="muted">{origin}</td></tr>)}</tbody>
          </table>
        </details>
      )}
      {readable && (noQuickPlot
        ? <div className="file-plot-note muted small">No quick plot: {noQuickPlot}.{" "}
            <button className="link" onClick={openPlot}>Open it in the Plot tab</button> (a new browser tab).</div>
        : <InlinePlot key={readable.path} file={readable} onSkip={setSkipped} />)}
      {text !== null && shown.more && (
        <div className="file-more muted small">
          Showing the first {size(shown.next)}{shown.total !== null ? ` of ${size(shown.total)}` : ""}, the rest not downloaded.{" "}
          {room > 0 ? <>
            <button className="small" disabled={loading} onClick={loadMore}>Load {size(Math.min(VIEW_BYTES, room))} more</button>{" "}
            {wholeFits && <button className="small" disabled={loading} onClick={loadWhole}>Load whole file</button>}
            {!wholeFits && <> More than {size(VIEW_MAX)} is not shown here: Download it{readable ? ", or plot it" : ""}.</>}
          </> : <>The viewer shows {size(VIEW_MAX)} at most: Download it{readable ? ", or plot it" : ""} for the rest.</>}
          {loading && <span> Loading…</span>}
        </div>
      )}
      {IMAGE.test(path) && <img className="file-image" src={href} alt={path} />}
      {!IMAGE.test(path) && !isText && <p className="muted">Binary file: download it to open it.</p>}
      {text !== null && /\.(csv|tsv)$/i.test(path) && mode === "auto" && <CsvTable key={href} text={text} tab={/\.tsv$/i.test(path)} partial={shown.more}
        onMore={shown.more && room > 0 && !loading ? loadMore : undefined} />}
      {text !== null && !(/\.(csv|tsv)$/i.test(path) && mode === "auto") && (
        /\.(log|txt)$/i.test(path) && !rendered?.values?.length && !line ? <pre className="log">{text}</pre>
          : <CodeEditor uri={`file:///view/${encodeURIComponent(href)}`} value={text} readOnly language={languageOf(path)} height="calc(100vh - 260px)"
            values={rendered?.values} revealLine={line} />
      )}
    </div>
  );
}

const CSV_ROWS = 1000;

/** A CSV text as a table, CSV_ROWS rows at first, more on demand; partial: the text is the start of the file
 *  (its last line may be cut: left out), onMore loads more of it. */
function CsvTable({ text, tab, partial, onMore }: { text: string; tab?: boolean; partial?: boolean; onMore?: () => void }) {
  const [limit, setLimit] = useState(CSV_ROWS);
  const lines = useMemo(() => {
    const all = text.split(/\r?\n/);
    if (partial) all.pop();
    return all.filter(Boolean);
  }, [text, partial]);
  const sep = useMemo(() => {
    const first = lines[0] || "";
    return tab ? "\t" : first.includes(";") && !first.includes(",") ? ";" : ",";
  }, [lines, tab]);
  const rows = useMemo(() => lines.slice(0, limit + 1).map((line) => line.split(sep)), [lines, limit, sep]);
  if (!rows.length) return <p className="muted">{partial ? "No complete line yet: load more of the file." : "Empty file."}</p>;
  const hidden = lines.length - rows.length;
  return (
    <div className="csv">
      <table className="grid compact">
        <thead><tr>{rows[0].map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
        <tbody>{rows.slice(1).map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
      </table>
      <p className="muted small">
        {(rows.length - 1).toLocaleString("en-US")} rows shown{hidden > 0 ? `, ${hidden.toLocaleString("en-US")} more loaded` : ""}
        {partial ? ", the file goes on" : ""}.{" "}
        {hidden > 0 && <button className="small" onClick={() => setLimit(limit + CSV_ROWS)}>Show {Math.min(CSV_ROWS, hidden).toLocaleString("en-US")} more rows</button>}
        {hidden === 0 && onMore && <button className="small" onClick={onMore}>Load more of the file</button>}
        {hidden === 0 && partial && !onMore && " Download the file for the rest."}
      </p>
    </div>
  );
}
