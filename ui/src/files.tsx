// File tree with viewers for text, YAML, CSV, logs and images, plus downloads (sequencer README, section 13.1).
import { useEffect, useMemo, useState } from "react";
import { get, url } from "./api";
import { CodeEditor, languageOf } from "./monaco";
import { classes, size } from "./util";

export interface Entry {
  name: string;
  path: string;
  dir: boolean;
  size: number | null;
}

const TEXT = /\.(ya?ml|json|txt|log|csv|tsv|md|ini|cfg|conf|sh|py|xml|html?|j2|toml|propj)$/i;
const IMAGE = /\.(png|jpe?g|gif|svg|webp)$/i;

/** Browses a folder API: base + "/" + path lists a folder (JSON array) or downloads a file. */
export function FileBrowser({ base, query, initial }: { base: string; query?: string; initial?: string }) {
  const [open, setOpen] = useState<Record<string, Entry[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set([""]));
  const [selected, setSelected] = useState<Entry | null>(null);
  const [error, setError] = useState<string | null>(null);
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
    load("");
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
        {selected ? <FileView href={url(`${base}/${selected.path}${q}`)} path={selected.path} /> : <p className="muted">Pick a file to see it.</p>}
      </div>
    </div>
  );
}

export function FileView({ href, path }: { href: string; path: string }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"auto" | "raw">("auto");
  useEffect(() => {
    setText(null);
    setError(null);
    if (IMAGE.test(path) || !TEXT.test(path)) return;
    fetch(href)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        const blob = await r.blob();
        if (blob.size > 5 << 20) return setText((await blob.slice(0, 5 << 20).text()) + "\n… (truncated: download the whole file)");
        setText(await blob.text());
      })
      .catch((e) => setError(e.message));
  }, [href, path]);
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
      {IMAGE.test(path) && <img className="file-image" src={href} alt={path} />}
      {!IMAGE.test(path) && !TEXT.test(path) && <p className="muted">Binary file: download it to open it.</p>}
      {text !== null && /\.(csv|tsv)$/i.test(path) && mode === "auto" && <CsvTable text={text} tab={/\.tsv$/i.test(path)} />}
      {text !== null && !(/\.(csv|tsv)$/i.test(path) && mode === "auto") && (
        /\.(log|txt)$/i.test(path) ? <pre className="log">{text}</pre>
          : <CodeEditor uri={`file:///view/${encodeURIComponent(href)}`} value={text} readOnly language={languageOf(path)} height="calc(100vh - 260px)" />
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
