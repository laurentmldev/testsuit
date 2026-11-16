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

// The keys explorer of a slot (Datapacks page): every key its template files and the commands of its steps can
// use, as trees to browse, filter and copy as {{ key }}. Asked from the server only when it is opened.

import { useEffect, useMemo, useState } from "react";
import { post } from "../api";
import { Dialog } from "../fields";
import { classes, notify, Spinner } from "../util";

function matches(path: string, value: any, filter: string): boolean {
  if (!filter) return true;
  if (path.toLowerCase().includes(filter)) return true;
  if (value !== null && typeof value === "object") return Object.entries(value).some(([k, v]) => matches(`${path}.${k}`, v, filter));
  return String(value).toLowerCase().includes(filter);
}

function copy(path: string) {
  const text = `{{ ${path} }}`;
  navigator.clipboard?.writeText(text).then(() => notify(`${text} copied`), () => notify(text));
}

function Node({ name, path, value, filter, depth }: { name: string; path: string; value: any; filter: string; depth: number }) {
  const branch = value !== null && typeof value === "object";
  const [open, setOpen] = useState(false);
  const shown = open || (filter !== "" && branch);
  if (!matches(path, value, filter)) return null;
  const entries = branch ? (Array.isArray(value) ? value.map((v, i) => [String(i), v] as [string, any]) : Object.entries(value)) : [];
  return (
    <li>
      {branch ? (
        <button className="link mono small" onClick={() => setOpen(!open)}>{shown ? "▾" : "▸"} {name}</button>
      ) : <span className="mono small">{name}</span>}
      <button className="link small key-copy" title={`Copy {{ ${path} }}`} onClick={() => copy(path)}>{"{{ }}"}</button>
      {branch
        ? !shown && <span className="muted small"> {Array.isArray(value) ? `${value.length} items` : `${entries.length} keys`}</span>
        : <span className="mono small key-value"> {value === "" ? <span className="muted">(empty)</span> : JSON.stringify(value)}</span>}
      {branch && shown && (
        <ul className="key-tree">
          {entries.map(([k, v]) => <Node key={k} name={k} path={Array.isArray(value) ? `${path}[${k}]` : `${path}.${k}`} value={v} filter={filter} depth={depth + 1} />)}
        </ul>
      )}
    </li>
  );
}

function Tree({ keys, filter }: { keys: Record<string, any>; filter: string }) {
  const names = useMemo(() => Object.keys(keys).sort(), [keys]);
  return <ul className="key-tree root">{names.map((k) => <Node key={k} name={k} path={k} value={keys[k]} filter={filter} depth={0} />)}</ul>;
}

export function KeysExplorer({ sid, testdef, config, slot, onClose }: {
  sid: string; testdef: string; config: string | null; slot: string; onClose: () => void;
}) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [where, setWhere] = useState<"run" | "generation">("run");
  const [filter, setFilter] = useState("");
  useEffect(() => {
    post(`edit/sessions/${sid}/slot_keys`, { testdef, slot, setup_config: config }).then(setData).catch((e) => setError(e.message));
  }, [sid, testdef, slot, config]);
  const keys = data ? (where === "run" ? data.run : data.generation) : null;
  const functions: string[] = data ? (where === "run" ? data.run_functions : data.generation_functions) || [] : [];
  return (
    <Dialog title={`Keys of slot ${slot}`} onClose={onClose} wide>
      {error && <pre className="error-box">{error}</pre>}
      {!data && !error && <Spinner text="Gathering the keys…" />}
      {data && (
        <div className="keys-explorer">
          <div className="row">
            <div className="segmented">
              <button className={classes(where === "run" && "selected")} onClick={() => setWhere("run")}
                title="The keys of the commands its steps run: those of the datapack, of the run and of the lab component it would be bound to">In its commands</button>
              <button className={classes(where === "generation" && "selected")} onClick={() => setWhere("generation")}
                title="The keys of its template files, rendered when the datapack is generated">In its template files</button>
            </div>
            <input type="search" placeholder="Filter names and values" value={filter} onChange={(e) => setFilter(e.target.value.toLowerCase())} autoFocus />
          </div>
          {where === "run" && (data.component
            ? <p className="small muted">Bound now to lab component <strong>{data.component}</strong>{data.stage && <> of stage <strong>{data.stage}</strong></>} (it may be another one when it runs). Values in &lt;…&gt; are known when it runs.</p>
            : <p className="banner warn">{data.why_not}</p>)}
          {where === "generation" && <p className="small muted">Values in &lt;…&gt; are known when the datapack is generated.</p>}
          {data.error && <p className="banner warn small">Some keys cannot be resolved, shown as written: {data.error}</p>}
          {keys && <Tree keys={keys} filter={filter} />}
          {functions.length > 0 && <p className="small">Functions: {functions.map((f) => <code key={f}>{f} </code>)}</p>}
          <p className="muted small">Click <span className="mono">{"{{ }}"}</span> to copy a key as a template.</p>
        </div>
      )}
    </Dialog>
  );
}
