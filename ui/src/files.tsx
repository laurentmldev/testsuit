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
import { useEffect, useMemo, useState } from "react";
import { parse } from "yaml";
import { get, url } from "./api";
import { CodeEditor, languageOf, type ValueHover } from "./monaco";
import { classes, size } from "./util";

export interface Entry {
  name: string;
  path: string;
  dir: boolean;
  size: number | null;
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

/** Browses a folder API: base + "/" + path lists a folder (JSON array) or downloads a file. */
export function FileBrowser({ base, query, initial }: { base: string; query?: string; initial?: string }) {
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
    if (initial) setSelected({ name: initial.split("/").pop()!, path: initial, dir: false, size: null });
  }, [initial]);
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
        {selected ? <FileView href={url(`${base}/${selected.path}${q}`)} path={selected.path} rendered={rendered[selected.path]} /> : <p className="muted">Pick a file to see it.</p>}
      </div>
    </div>
  );
}

export function FileView({ href, path, rendered }: { href: string; path: string; rendered?: RenderedFile }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"auto" | "raw">("auto");
  // a file rendered from a template is text, whatever its extension
  const isText = TEXT.test(path) || !!rendered;
  useEffect(() => {
    setText(null);
    setError(null);
    if (IMAGE.test(path) || !isText) return;
    fetch(href)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        const blob = await r.blob();
        if (blob.size > 5 << 20) return setText((await blob.slice(0, 5 << 20).text()) + "\n… (truncated: download the whole file)");
        setText(await blob.text());
      })
      .catch((e) => setError(e.message));
  }, [href, path, isText]);
  const keys = Object.entries(rendered?.keys || {});
  return (
    <div className="file-view">
      <div className="file-view-head">
        <code>{path}</code>
        <span className="spacer" />
        {/\.(csv|tsv)$/i.test(path) && (
          <button className="small" onClick={() => setMode(mode === "auto" ? "raw" : "auto")}>{mode === "auto" ? "raw text" : "table"}</button>
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
      {IMAGE.test(path) && <img className="file-image" src={href} alt={path} />}
      {!IMAGE.test(path) && !isText && <p className="muted">Binary file: download it to open it.</p>}
      {text !== null && /\.(csv|tsv)$/i.test(path) && mode === "auto" && <CsvTable text={text} tab={/\.tsv$/i.test(path)} />}
      {text !== null && !(/\.(csv|tsv)$/i.test(path) && mode === "auto") && (
        /\.(log|txt)$/i.test(path) && !rendered?.values?.length ? <pre className="log">{text}</pre>
          : <CodeEditor uri={`file:///view/${encodeURIComponent(href)}`} value={text} readOnly language={languageOf(path)} height="calc(100vh - 260px)"
            values={rendered?.values} />
      )}
    </div>
  );
}

function CsvTable({ text, tab }: { text: string; tab?: boolean }) {
  const rows = useMemo(() => {
    const sep = tab ? "\t" : text.split("\n")[0].includes(";") && !text.split("\n")[0].includes(",") ? ";" : ",";
    return text.split(/\r?\n/).filter(Boolean).slice(0, 1001).map((line) => line.split(sep));
  }, [text, tab]);
  if (!rows.length) return <p className="muted">Empty file.</p>;
  return (
    <div className="csv">
      <table className="grid compact">
        <thead><tr>{rows[0].map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
        <tbody>{rows.slice(1).map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
      </table>
      {rows.length > 1000 && <p className="muted">First 1000 rows: download the file for the rest.</p>}
    </div>
  );
}
