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
// use, as trees to browse, filter and copy as {{ key }}. Asked from the server only when it is opened. Its text
// field filters the trees, or, in Evaluate mode, gives the value of a key expression.

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

type Origins = Record<string, { from: string; overrides?: { from: string; value: string }[] }>;

/** Where a key comes from: its own entry, or the nearest one above it (a list item: the list). */
function originOf(origins: Origins, path: string, exact: boolean) {
  if (origins[path] || exact) return origins[path];
  let p = path;
  while (p) {
    p = p.endsWith("]") ? p.slice(0, p.lastIndexOf("[")) : p.includes(".") ? p.slice(0, p.lastIndexOf(".")) : "";
    if (origins[p]) return origins[p];
  }
  return undefined;
}

function Origin({ origins, path, exact, above }: { origins: Origins; path: string; exact: boolean; above?: string }) {
  const o = originOf(origins, path, exact);
  if (!o || (!origins[path] && o.from === above)) return null;     // the same as the key above it: said once
  const over = o.overrides || [];
  return (
    <span className="small key-origin">
      <span className="muted"> · {o.from}</span>
      {over.length > 0 && (
        <span className="warn-text" title={over.map((x) => `${x.value} from ${x.from}`).join("\n")}>
          {" "}overrides <code>{over[0].value}</code> from {over[0].from}{over.length > 1 ? ` (and ${over.length - 1} more)` : ""}
        </span>
      )}
    </span>
  );
}

function Node({ name, path, value, filter, depth, origins, above }: {
  name: string; path: string; value: any; filter: string; depth: number; origins: Origins; above?: string;
}) {
  const here = originOf(origins, path, false)?.from;
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
      <Origin origins={origins} path={path} exact={branch} above={above} />
      {branch && shown && (
        <ul className="key-tree">
          {entries.map(([k, v]) => <Node key={k} name={k} path={Array.isArray(value) ? `${path}[${k}]` : `${path}.${k}`} value={v} filter={filter} depth={depth + 1} origins={origins} above={here} />)}
        </ul>
      )}
    </li>
  );
}

function Tree({ keys, filter, origins }: { keys: Record<string, any>; filter: string; origins: Origins }) {
  const names = useMemo(() => Object.keys(keys).sort(), [keys]);
  return <ul className="key-tree root">{names.map((k) => <Node key={k} name={k} path={k} value={keys[k]} filter={filter} depth={0} origins={origins} />)}</ul>;
}

export function KeysExplorer({ sid, testdef, config, slot, onClose }: {
  sid: string; testdef: string; config: string | null; slot: string; onClose: () => void;
}) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [where, setWhere] = useState<"run" | "generation">("generation");
  const [filter, setFilter] = useState("");
  const [text, setText] = useState("");
  const [evaluate, setEvaluate] = useState(false);
  const [result, setResult] = useState<{ expression: string; where: string; value?: any; error?: string | null } | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const run = (expression = text, w = where) => {
    if (!expression.trim()) { setResult(null); return; }
    setEvaluating(true);
    post(`edit/sessions/${sid}/eval_key`, { testdef, slot, setup_config: config, expression, where: w })
      .then((r) => setResult({ expression, where: w, ...r }))
      .catch((e) => setResult({ expression, where: w, error: e.message }))
      .finally(() => setEvaluating(false));
  };
  const changeWhere = (w: "run" | "generation") => { setWhere(w); if (evaluate && result) run(text, w); };
  // Evaluate evaluates the text, every time it is clicked; Filter goes back to filtering the trees with it.
  const startEvaluate = () => {
    if (!evaluate) { setEvaluate(true); setFilter(""); setResult(null); }
    run(text);
  };
  const stopEvaluate = () => {
    setEvaluate(false);
    setFilter(text.toLowerCase());
    setResult(null);
  };
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
              <button className={classes(where === "generation" && "selected")} onClick={() => changeWhere("generation")}
                title="The keys of the configuration: its template files see them all, its commands the dictionary and built-in ones">Config and runtime</button>
              <button className={classes(where === "run" && "selected")} onClick={() => changeWhere("run")}
                title="The keys only the commands of its steps have: of the run and of the lab component it would be bound to">Runtime-only</button>
            </div>
            <input type="search" className={classes(evaluate && "mono")} autoFocus value={text}
              placeholder={evaluate ? "Key expression, e.g. lab.components[slots.my_slot.component].host" : "Filter names and values"}
              onChange={(e) => { setText(e.target.value); if (!evaluate) setFilter(e.target.value.toLowerCase()); }}
              onKeyDown={(e) => { if (evaluate && e.key === "Enter") run(); }} />
            <button className="primary" onClick={startEvaluate} disabled={evaluating || (evaluate && !text.trim())}
              title="Evaluate the text as a key expression, as {{ … }} in a template or a command would (Enter does it too)">
              {evaluating ? "Evaluating…" : "Evaluate"}</button>
            {evaluate && <button onClick={stopEvaluate} title="Back to filtering the keys with the text">Filter</button>}
          </div>
          {evaluate && (evaluating ? <Spinner text="Evaluating…" />
            : result ? (
              <div className={classes("key-eval", result.error ? "bad" : "ok")}>
                <code>{`{{ ${result.expression.trim().replace(/^\{\{\s*|\s*\}\}$/g, "")} }}`}</code>
                <span className="muted small"> {result.where === "run" ? "in the commands" : "at generation"}</span>
                {result.error
                  ? <pre className="error-box">{result.error}</pre>
                  : <pre className="mono small">{typeof result.value === "string" ? result.value : JSON.stringify(result.value, null, 2)}</pre>}
              </div>)
            : <p className="small muted">Type an expression of these keys, without or with its <code>{"{{ }}"}</code>, and press Enter: its value {where === "run" ? "in the commands of its steps" : "at generation"} (switch above). Filters work too: <code>{"slot.id | upper"}</code>.</p>)}
          {where === "run" && (data.component
            ? <p className="small muted">Only in the commands of its steps (and their <code>env</code>, <code>wait_for</code>…), rendered when the run executes them: the slot is then bound to a lab component, now <strong>{data.component}</strong>{data.stage ? <> of stage <strong>{data.stage}</strong></> : <>, ad hoc: the slot has no <code>stage_slot</code>, so its <code>stage.*</code> keys are empty, at run time too (the other components are under <code>lab.components.*</code>)</>} (it may be another one when it runs). Values in &lt;…&gt; are known then. <code>slot</code> here is its run-time form; a template file cannot use these keys. For example, the address of the host the slot runs on is <code>{"{{ component.host }}"}</code>: it depends on the lab component the run is given, so a command can read it, a template cannot. The host bound to another slot, <code>my_slot</code>: <code>{"{{ lab.components[slots.my_slot.component].host }}"}</code>.</p>
            : <p className="banner warn">{data.why_not}</p>)}
          {where === "generation" && <p className="small muted">The dictionary and built-in keys, the same in its template files (rendered when the datapack is generated) and in its commands (frozen in the datapack). Only templates have <code>slot</code> in this form (<code>slot.configuration</code>, <code>slot.input_dir</code>), <code>slots.&lt;id&gt;</code> of the slots it needs and <code>read_input()</code>; commands have the runtime-only ones instead. Values in &lt;…&gt; are known at generation. For example, a delay before switching off written in a dictionary (<code>{"{{ power.switchoff_timeout_s }}"}</code>) is fixed by the configuration: templates and commands both read it.</p>}
          {data.error && <p className="banner warn small">Some keys cannot be resolved, shown as written: {data.error}</p>}
          {keys && <Tree keys={keys} filter={filter} origins={(where === "run" ? data.run_origins : data.generation_origins) || {}} />}
          {functions.length > 0 && <p className="small">Functions: {functions.map((f) => <code key={f}>{f} </code>)}</p>}
          <p className="muted small">Click <span className="mono">{"{{ }}"}</span> to copy a key as a template. After each value: where it is set, and the value it <span className="warn-text">overrides</span>, set at a lower level (a dictionary of the setup, under one of the test definition; the inventory groups, under the stage, under the host).</p>
        </div>
      )}
    </Dialog>
  );
}
