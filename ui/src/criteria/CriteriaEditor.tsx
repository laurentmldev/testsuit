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

// The criteria editor: the scenarios of a folder and their unit tests. Scenario files with completion
// (criteria, their fields, helpers, computed parameters, parameters of the data, keys), problems as
// markers, the help of the criterion under the cursor and a palette of checks to add; the unit tests
// with their coverage; the data of the cases as a table and a plot. Shared by tsqd (on an editing
// session) and `mxp edit` (on a folder): everything goes through the adapter.
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../api";
import { Dialog } from "../fields";
import { CodeEditor, type Marker } from "../monaco";
import { attempt, classes, Markdown, notify, relPath, Spinner } from "../util";
import type { CriteriaAdapter, CriterionInfo, Description, FileDoc, Overview, Problem, ScenarioInfo, UtestsInfo } from "./adapter";
import { DataView } from "./DataView";
import { setCriteriaContext } from "./expressions";
import { entries, entryAt, fieldsOf, freeEntryName, templateEntryName, templateNamed, type Entry } from "./scenario";
import { UnitTests } from "./UnitTests";
import { UTESTS_SCHEMA } from "./utestsSchema";

type SaveState = "saved" | "pending" | "saving" | "error" | "conflict";
const SAVE_LABEL: Record<SaveState, string> = { saved: "saved", pending: "editing…", saving: "saving…", error: "not saved", conflict: "not saved: changed elsewhere" };

const isScenarioFile = (p: string) => /\.mexploit\.ya?ml$/.test(p);
const isUtestsFile = (p: string) => /(^|\/)(utests|[^/]+\.utests)\.ya?ml$/.test(p);
const isTable = (p: string) => /\.(csv|tsv|txt)$/i.test(p);
const base = (p: string) => p.split("/").pop() || p;
const dirOf = (p: string) => p.split("/").slice(0, -1).join("/");

function scenarioOf(ov: Overview | null, path: string | null): ScenarioInfo | null {
  if (!ov || !path) return null;
  const direct = ov.scenarios.find((s) => s.files.includes(path) || s.critlibs.includes(path));
  if (direct) return direct;
  const u = ov.utests.find((x) => x.path === path || x.cases?.some((c) => c.data_files.includes(path)));
  if (u?.scenario) return ov.scenarios.find((s) => s.path === u.scenario || s.files.includes(u.scenario!)) || null;
  return null;
}

export function CriteriaEditor({ adapter, scope, file, onFile, bar }: {
  adapter: CriteriaAdapter;
  /** shown above the editor: what it edits, and the host's own actions */
  bar?: (ov: Overview | null) => ReactNode;
  /** only the scenarios under this folder (a test definition) */
  scope?: string | null;
  file: string | null;
  onFile: (path: string | null) => void;
}) {
  const [ov, setOv] = useState<Overview | null>(null);
  const [ovError, setOvError] = useState<string | null>(null);
  const reload = useCallback(() => adapter.overview().then((o) => { setOv(o); setOvError(null); }).catch((e) => setOvError(e.message)), [adapter]);
  useEffect(() => { reload(); }, [reload]);

  const scenarios = useMemo(() => (ov?.scenarios || []).filter((s) => !scope || s.path === scope || s.path.startsWith(scope.replace(/\/?$/, "/"))), [ov, scope]);
  const scenario = scenarioOf(ov, file);
  // a file to start with: the first scenario file
  useEffect(() => {
    if (ov && !file && scenarios[0]?.files[0]) onFile(scenarios[0].files[0]);
  }, [ov, file, scenarios, onFile]);

  // ── the open file ─────────────────────────────────────────────────
  const [doc, setDoc] = useState<FileDoc | null>(null);
  const [text, setText] = useState("");
  const [state, setState] = useState<SaveState>("saved");
  const [external, setExternal] = useState<{ current: FileDoc | null } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reveal, setReveal] = useState<{ line: number } | null>(null);
  const [cursor, setCursor] = useState(1);
  const [view, setView] = useState<"data" | "text">("data");
  const textRef = useRef(text);
  textRef.current = text;
  const docRef = useRef(doc);
  docRef.current = doc;

  useEffect(() => {
    setDoc(null);
    setExternal(null);
    setLoadError(null);
    setState("saved");
    setCursor(1);
    if (!file) return;
    let current = true;
    adapter.read(file).then((d) => { if (current) { setDoc(d); setText(d.content); } }).catch((e) => current && setLoadError(e.message));
    return () => { current = false; };
  }, [adapter, file]);

  const save = useCallback(async (force = false) => {
    const d = docRef.current;
    if (!d) return;
    const content = textRef.current;
    setState("saving");
    try {
      const written = await adapter.write(d.path, content, force ? null : d.version);
      if (docRef.current?.path !== d.path) return;
      setDoc({ ...written, content });
      setExternal(null);
      setState(textRef.current === content ? "saved" : "pending");
      if (isUtestsFile(d.path) || force) reload();
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 409) {
        setState("conflict");
        setExternal({ current: e.body?.current || null });
      } else {
        setState("error");
        notify(`${d.path}: ${e.message}`, "error");
      }
    }
  }, [adapter, reload]);

  // saved 1 s after the last change
  useEffect(() => {
    if (!doc || text === doc.content || state === "conflict") return;
    setState("pending");
    const t = window.setTimeout(() => save(), 1000);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  // changed elsewhere (another browser, a git pull, an editor)?
  useEffect(() => {
    if (!doc) return;
    const t = window.setInterval(async () => {
      if (state !== "saved" || external) return;
      try {
        const v = await adapter.version(doc.path);
        if (v !== docRef.current?.version) setExternal({ current: v === null ? null : await adapter.read(doc.path) });
      } catch { /* the server is away for a moment */ }
    }, 3000);
    return () => window.clearInterval(t);
  }, [adapter, doc, state, external]);

  const loadTheirs = () => {
    if (!external?.current) return;
    setDoc(external.current);
    setText(external.current.content);
    setExternal(null);
    setState("saved");
  };

  // ── what the scenario can use ─────────────────────────────────────
  const [desc, setDesc] = useState<Description | null>(null);
  const [descError, setDescError] = useState<string | null>(null);
  const [params, setParams] = useState<string[]>([]);
  const [keys, setKeys] = useState<{ label: string; detail?: string; doc?: string }[]>([]);
  const [keysInfo, setKeysInfo] = useState<{ origin: string; error: string | null; count: number } | null>(null);
  const scenarioPath = scenario?.path || null;
  const critlibStamp = scenario?.critlibs.join(",");
  const shownScenario = useRef(scenarioPath);
  shownScenario.current = scenarioPath;
  const refreshDescribe = useCallback(() => {
    setDescError(null);
    adapter.describe(scenarioPath).then((d) => shownScenario.current === scenarioPath && setDesc(d))
      .catch((e) => shownScenario.current === scenarioPath && setDescError(e.message));
  }, [adapter, scenarioPath]);
  useEffect(() => {
    refreshDescribe();
    setParams([]);
    setKeys([]);
    setKeysInfo(null);
    if (!scenarioPath) return;
    let current = true;
    adapter.params(scenarioPath).then((p) => current && setParams(p.params)).catch(() => {});
    adapter.keys(scenarioPath).then((k) => {
      if (!current) return;
      setKeys(k.keys.map((x) => ({ label: x.key, detail: x.value, doc: `from ${x.origin}` })));
      setKeysInfo({ origin: k.origin, error: k.error, count: k.keys.length });
    }).catch(() => {});
    return () => { current = false; };
  }, [adapter, scenarioPath, critlibStamp, refreshDescribe]);

  // the scenario's files as written now, for its checks and their lines
  const [texts, setTexts] = useState<Record<string, string>>({});
  useEffect(() => {
    setTexts({});
    if (!scenario) return;
    let current = true;      // the answers for a scenario no longer shown are dropped
    Promise.all(scenario.files.map((f) => adapter.read(f).then((d) => [f, d.content] as const).catch(() => [f, ""] as const)))
      .then((pairs) => current && setTexts(Object.fromEntries(pairs)));
    return () => { current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, scenarioPath, scenario?.files.join(",")]);
  const allTexts = useMemo(() => (file && isScenarioFile(file) && doc ? { ...texts, [file]: text } : texts), [texts, file, doc, text]);
  const allEntries = useMemo(() => Object.entries(allTexts).sort(([a], [b]) => a.localeCompare(b))
    .flatMap(([f, t]) => entries(t).map((e) => ({ ...e, file: f }))), [allTexts]);
  const locate = useCallback((check: string) => {
    const e = allEntries.find((x) => x.name === check);
    return e ? { file: e.file, line: e.line } : null;
  }, [allEntries]);
  const scenarioChecks = useMemo(() => allEntries.filter((e) => e.type).map((e) => ({ name: e.name, asserts: desc?.criteria[e.type!]?.asserts ?? true })), [allEntries, desc]);

  // ── problems ──────────────────────────────────────────────────────
  const [problems, setProblems] = useState<Problem[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [linting, setLinting] = useState(false);
  useEffect(() => {
    if (!scenario) { setProblems([]); return; }
    const target = file && isScenarioFile(file) ? file : scenario.files[0];
    if (!target) return;
    let current = true;
    const t = window.setTimeout(() => {
      setLinting(true);
      adapter.lint(target, file && isScenarioFile(file) && doc ? text : undefined)
        .then((r) => { if (current) { setProblems(r.problems); setNotes(r.notes); } })
        .catch((e) => { if (current) { setProblems([{ file: target, line: null, severity: "error", message: e.message }]); setNotes([]); } })
        .finally(() => setLinting(false));
    }, 700);
    return () => { current = false; window.clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, scenarioPath, file, text, desc]);
  const markers: Marker[] = useMemo(() => problems.filter((p) => p.file === file).map((p) => ({ line: p.line || 1, message: p.message, severity: p.severity })), [problems, file]);

  // ── editor context: completion of expressions and parameter names ──
  const uri = file ? `file:///criteria/${encodeURIComponent(adapter.label)}/${file}` : "";
  useEffect(() => {
    if (!uri || !file || !isScenarioFile(file)) return;
    setCriteriaContext(uri, { describe: desc, params });
    return () => setCriteriaContext(uri, null);
  }, [uri, file, desc, params]);
  const schema = file && isScenarioFile(file) ? desc?.scenario_schema ?? null : file && isUtestsFile(file) ? UTESTS_SCHEMA : null;

  const open = useCallback((path: string, line?: number) => {
    if (path !== file) onFile(path);
    if (line) window.setTimeout(() => setReveal({ line }), path !== file ? 400 : 0);
  }, [file, onFile]);

  // ── adding ────────────────────────────────────────────────────────
  const fileEntries = useMemo(() => (file && isScenarioFile(file) ? entries(text) : []), [file, text]);
  const addCheck = (type: string, crit: CriterionInfo) => {
    if (!file || !isScenarioFile(file)) return;
    const name = freeEntryName(allEntries, templateEntryName(crit.template, type));
    const block = templateNamed(crit.template, type, name);
    const current = entryAt(fileEntries, cursor);
    const lines = text.replace(/\s+$/, "").split("\n");
    const at = current ? Math.min(current.end, lines.length) : lines.length;
    // after the entry under the cursor (and its trailing blank lines), else at the end
    let insertAt = at;
    while (insertAt > 0 && !lines[insertAt - 1].trim()) insertAt--;
    const next = [...lines.slice(0, insertAt), "", ...block.replace(/\n$/, "").split("\n"), ...(insertAt < lines.length ? [""] : []), ...lines.slice(insertAt)];
    setText(next.join("\n").replace(/^\n+/, "") + "\n");
    window.setTimeout(() => setReveal({ line: insertAt + 2 }), 50);
  };
  const [creating, setCreating] = useState<null | { title: string; path: string; content: string; help: string }>(null);
  const create = async (path: string, content: string) => {
    const done = await attempt(() => adapter.write(path, content, null, true), `${path} created`);
    if (done) {
      setCreating(null);
      await reload();
      onFile(path);
    }
  };

  const utestsOf = (s: ScenarioInfo) => (ov?.utests || []).filter((u) => s.utests.includes(u.path));
  const [utestsPick, setUtestsPick] = useState<string | null>(null);
  const scenarioUtests = scenario ? utestsOf(scenario) : [];
  const shownUtests: UtestsInfo | null = scenarioUtests.find((u) => u.path === utestsPick) || (file && scenarioUtests.find((u) => u.path === file)) || scenarioUtests[0] || null;
  const [utestsOpen, setUtestsOpen] = useState(true);

  const entry = file && isScenarioFile(file) ? entryAt(fileEntries, cursor) : null;
  const tableView = !!file && isTable(file) && view === "data";

  return (
    <div className="crit-editor">
      {bar?.(ov)}
      <div className="crit-main">
        <aside className="file-tree crit-tree">
          {ovError && <div className="error-box">{ovError}</div>}
          {!ov && !ovError && <Spinner text="Reading the folder…" />}
          {ov && !scenarios.length && <p className="muted crit-empty">No scenario here: a scenario is a folder of <code>*.mexploit.yml</code> files (usually <code>criteria/&lt;name&gt;/</code> in a test definition).</p>}
          {scenarios.map((s) => (
            <ScenarioTree key={s.path} s={s} utests={utestsOf(s)} current={file} problems={s.path === scenarioPath ? problems : []} open={open}
              onNewFile={() => setCreating({ title: "New scenario file", path: `${s.path}/${s.name}_more.mexploit.yml`, help: "Scenario files of a folder run in the order of their names.",
                content: `# More checks of the ${s.name} scenario (mexploit).\n` })}
              onNewUtests={() => {
                const dir = /(^|\/)criteria\/[^/]+$/.test(s.path) ? s.path.replace(/(^|\/)criteria\/([^/]+)$/, "$1criteria_utests/$2") : `${dirOf(s.path) ? dirOf(s.path) + "/" : ""}criteria_utests/${s.name}`;
                const path = `${dir}/utests.yml`;
                setCreating({ title: "Unit tests of the scenario", path, help: "Next to criteria/, in criteria_utests/: never copied into a datapack.", content: utestsSkeleton(s, path) });
              }}
              onNewData={(dataDir) => setCreating({ title: "New data file of the case", path: `${dataDir.replace(/\/$/, "")}/data.csv`, help: "A table made by hand: a header line, then a value per column, separated by ; (a first column of times).",
                content: "Time;value\n0;0\n1;0\n" })} />
          ))}
        </aside>

        <section className="crit-center">
          {file ? (
            <div className={classes("doc-head", isScenarioFile(file) || isUtestsFile(file) ? "kind-criteria" : "kind-other")}>
              <span className="kind-chip">{isScenarioFile(file) ? "Scenario" : isUtestsFile(file) ? "Unit tests" : isTable(file) ? "Data" : /\.py$/.test(file) ? "Library of criteria" : "File"}</span>
              <span className="doc-path" title={file}>{dirOf(file) && <span className="doc-dir">{dirOf(file)}/</span>}<span className="doc-base">{base(file)}</span></span>
              {isTable(file) && <div className="segmented">
                <button className={view === "data" ? "selected" : ""} onClick={() => setView("data")}>Table and plot</button>
                <button className={view === "text" ? "selected" : ""} onClick={() => setView("text")}>Text</button>
              </div>}
              <span className={classes("save-state", state)}>{doc ? SAVE_LABEL[state] : ""}</span>
              {linting && <Spinner />}
            </div>
          ) : null}
          {loadError && <div className="error-box">{loadError}</div>}
          {external && (
            <div className="banner warn">
              <span>{external.current ? "This file was changed elsewhere" : "This file was deleted elsewhere"} since you opened it.{state === "conflict" ? " Your last changes are not saved." : ""} Which version do you keep?</span>
              {external.current && <button className="small" onClick={loadTheirs}>Load that version</button>}
              <button className="small" onClick={() => save(true)}>{external.current ? "Overwrite it with mine" : "Save mine again"}</button>
            </div>
          )}
          <div className="crit-doc">
            {doc && file && (tableView ? <DataView text={text} /> : (
              <CodeEditor uri={uri} value={text} onChange={setText} schema={schema} keys={keys} markers={markers}
                onSave={() => save()} reveal={reveal} onCursor={setCursor} sections={isScenarioFile(file)} />
            ))}
            {!doc && file && !loadError && <Spinner text="Loading…" />}
          </div>
          {scenario && (
            <div className={classes("crit-utests", !utestsOpen && "folded")}>
              <button className="fold" onClick={() => setUtestsOpen(!utestsOpen)} title={utestsOpen ? "Fold" : "Unfold"}>{utestsOpen ? "▾" : "▸"}</button>
              {scenarioUtests.length > 1 && (
                <select value={shownUtests?.path || ""} onChange={(e) => setUtestsPick(e.target.value)}>
                  {scenarioUtests.map((u) => <option key={u.path} value={u.path}>{u.path}</option>)}
                </select>
              )}
              {utestsOpen && (shownUtests ? (
                <UnitTests adapter={adapter} utests={shownUtests} onOpen={open} locate={locate} scenarioChecks={scenarioChecks} onRan={reload} />
              ) : (
                <div className="utests"><div className="utests-bar"><strong>Unit tests</strong>
                  <span className="muted small">None for this scenario yet: small data cases, each with the status expected of each check.</span></div></div>
              ))}
            </div>
          )}
        </section>

        <aside className="session-side crit-side">
          <EntryHelp entry={entry} desc={desc} open={open} />
          <Palette desc={desc} error={descError} canAdd={!!file && isScenarioFile(file) && !!doc} onAdd={addCheck} onRefresh={refreshDescribe} />
          <h4>Problems {scenario && <span className="muted small">of {scenario.name}</span>}</h4>
          {notes.map((n, i) => <div key={i} className="banner info small">{n}</div>)}
          {scenario ? (problems.length ? (
            <div className="problems">
              {problems.map((p, i) => (
                <div key={i} className={classes("problem", p.severity)} onClick={() => open(p.file, p.line || undefined)}>
                  <span className="where">{base(p.file)}{p.line ? `:${p.line}` : ""}{p.entry ? ` · ${p.entry}${p.field ? "." + p.field : ""}` : ""}</span>{p.message}
                </div>
              ))}
            </div>
          ) : <div className="problems ok">No problem found.</div>) : <p className="muted small">Open a scenario file.</p>}
          {keysInfo && (
            <details className="crit-keys">
              <summary>Keys ({keysInfo.count}) <span className="muted small">{keysInfo.origin}</span></summary>
              {keysInfo.error && <div className="error-box">{keysInfo.error}</div>}
              <table className="kv small"><tbody>{keys.slice(0, 200).map((k) => <tr key={k.label}><td><code>{k.label}</code></td><td>{k.detail}</td></tr>)}</tbody></table>
              <p className="muted small">Type <code>{"{{"}</code> in a file to insert one.</p>
            </details>
          )}
        </aside>
      </div>
      {creating && <CreateDialog {...creating} onClose={() => setCreating(null)} onCreate={create} />}
    </div>
  );
}

function utestsSkeleton(s: ScenarioInfo, path: string): string {
  const scenario = relPath(path, s.path);
  const dicts = s.dictionaries.map((d) => relPath(path, d));
  return `# Unit tests of the ${s.name} criteria (mxp utest): small data cases, each with what every check must
# give on it. A check not listed under 'expect' must succeed.
scenario: ${scenario}
${dicts.length ? `dictionaries: [${dicts.join(", ")}]` : "# dictionaries: [../../scenario.yml]     # keys rendering the scenario"}

cases:
  nominal:
    description: Every check passes.
    data: nominal/
`;
}

function ScenarioTree({ s, utests, current, problems, open, onNewFile, onNewUtests, onNewData }: {
  s: ScenarioInfo; utests: UtestsInfo[]; current: string | null; problems: Problem[]; open: (p: string) => void;
  onNewFile: () => void; onNewUtests: () => void; onNewData: (dir: string) => void;
}) {
  const count = (f: string) => {
    const mine = problems.filter((p) => p.file === f);
    const e = mine.filter((p) => p.severity === "error").length;
    return e ? <span className="count bad">{e}</span> : mine.length ? <span className="count warn">{mine.length}</span> : null;
  };
  const row = (path: string, label: string, extra?: ReactNode, depth = 0) => (
    <div key={path} className={classes("tree-row", current === path && "selected")} style={{ paddingLeft: 8 + depth * 12 }} onClick={() => open(path)} title={path}>
      <span className="tree-name">{label}</span>{extra}
    </div>
  );
  const last = utests.find((u) => u.last)?.last;
  return (
    <div className="tree-group crit-scenario">
      <div className="tree-group-title" title={s.path}>
        <span>{s.name}</span>
        {last && <span className={classes("badge", last.ok && last.fully_covered ? "ok" : last.ok ? "warn" : "bad")}
          title={`${last.cases_ok}/${last.cases} case(s) as expected; ${last.fully_covered ? "every check covered" : "some checks not covered"}`}>
          {last.ok ? (last.fully_covered ? "covered" : "partly covered") : "failing"}</span>}
      </div>
      <div className="tree-sub muted small">{s.testdef ? `test ${s.testdef.split("/").slice(-2, -1)[0] || s.testdef}` : s.path}</div>
      {s.files.map((f) => row(f, base(f), count(f)))}
      {s.critlibs.map((f) => row(f, base(f), <span className="badge">library</span>))}
      <button className="link small tree-more" onClick={onNewFile}>+ scenario file</button>
      {utests.map((u) => (
        <div key={u.path}>
          {row(u.path, `unit tests · ${base(u.path)}`, u.error ? <span className="count bad">!</span> : null)}
          {(u.cases || []).map((c) => (
            <div key={c.name}>
              <div className="tree-row case-row" style={{ paddingLeft: 20 }} title={c.description}>
                <span className="tree-name muted">{c.name}</span>
                {c.data_elsewhere && <span className="muted small" title={c.data.join(", ")}>data outside</span>}
                {c.data[0] && c.data_files.length === 0 && !c.data_elsewhere && <button className="link small" onClick={(e) => { e.stopPropagation(); onNewData(c.data[0]); }}>+ data</button>}
              </div>
              {c.data_files.slice(0, 8).map((f) => row(f, base(f), null, 2))}
            </div>
          ))}
        </div>
      ))}
      {!utests.length && <button className="link small tree-more" onClick={onNewUtests}>+ unit tests</button>}
    </div>
  );
}

function EntryHelp({ entry, desc, open }: { entry: Entry | null; desc: Description | null; open: (p: string) => void }) {
  if (!entry) {
    return (
      <div className="help-panel">
        <h4>Criteria</h4>
        <p className="muted">Put the cursor in an entry to see its criterion and fields. In a value, Ctrl+Space completes: the
          helpers and computed parameters in expressions, the parameters of the data in parameter names, <code>{"{{"}</code> the keys.</p>
      </div>
    );
  }
  const crit = entry.type ? desc?.criteria[entry.type] : undefined;
  if (!crit) {
    return <div className="help-panel"><h4>{entry.name}</h4><p className="muted">{entry.type ? `Unknown criterion “${entry.type}”.` : "Give it a type: the criterion it runs."}</p></div>;
  }
  const fields = fieldsOf(crit);
  return (
    <div className="help-panel crit-help">
      <h4>{entry.name} <span className="muted">· {entry.type}</span></h4>
      <div className="crit-badges">
        <span className="badge" title="Where the criterion comes from">{crit.source}</span>
        {crit.source_file && <button className="link small" onClick={() => open(crit.source_file!)}>{crit.source_file.split("/").pop()}</button>}
        <span className={classes("badge", crit.asserts ? "info" : "")}>{crit.asserts ? "can fail" : crit.defines_cp ? "computes cp." + entry.name : "computes or plots"}</span>
        {!crit.declared && <span className="badge warn" title="Its fields are read from its docstring: not checked">fields not declared</span>}
      </div>
      <Markdown text={crit.doc} />
      {fields.length > 0 && (
        <table className="crit-fields">
          <tbody>
            {fields.map((f) => (
              <tr key={f.name}>
                <td><code>{f.name}</code>{f.required && <span className="req" title="required">*</span>}</td>
                <td>
                  <span className="muted small">{f.type}{f.kind ? ` · ${f.kind.replace("_", " ")}` : ""}</span>
                  {f.help && <div className="small">{f.help}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Palette({ desc, error, canAdd, onAdd, onRefresh }: {
  desc: Description | null; error: string | null; canAdd: boolean; onAdd: (type: string, crit: CriterionInfo) => void; onRefresh: () => void;
}) {
  const [filter, setFilter] = useState("");
  const groups = useMemo(() => {
    const g: Record<string, [string, CriterionInfo][]> = {};
    for (const [name, c] of Object.entries(desc?.criteria || {})) {
      if (filter && !name.includes(filter.toLowerCase()) && !c.doc.toLowerCase().includes(filter.toLowerCase())) continue;
      (g[c.source] ||= []).push([name, c]);
    }
    return Object.entries(g).sort(([a], [b]) => (a === "builtin" ? -1 : b === "builtin" ? 1 : a.localeCompare(b)));
  }, [desc, filter]);
  return (
    <div className="crit-palette">
      <h4>Add a check <button className="link small" onClick={onRefresh} title="Read the criteria again (a library of criteria changed)">↻</button></h4>
      {error && <div className="error-box">{error}</div>}
      <input type="search" placeholder="Filter the criteria" value={filter} onChange={(e) => setFilter(e.target.value)} />
      {!canAdd && <p className="muted small">Open a scenario file to add a check to it.</p>}
      {groups.map(([source, list]) => (
        <div key={source} className="crit-pal-group">
          <div className="crit-pal-title">{source === "builtin" ? "testsuit" : source}</div>
          {list.map(([name, c]) => (
            <button key={name} className="crit-pal-item" disabled={!canAdd} onClick={() => onAdd(name, c)} title={c.doc}>
              <code>{name}</code><span className="muted small">{c.doc.split("\n")[0].slice(0, 90)}</span>
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

function CreateDialog({ title, path, content, help, onClose, onCreate }: {
  title: string; path: string; content: string; help: string; onClose: () => void; onCreate: (path: string, content: string) => void;
}) {
  const [value, setValue] = useState(path);
  return (
    <Dialog title={title} onClose={onClose} footer={<>
      <button onClick={onClose}>Cancel</button>
      <button className="primary" disabled={!value.trim()} onClick={() => onCreate(value.trim(), content)}>Create</button>
    </>}>
      <label className="field-label">File</label>
      <input className="wide" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
      <p className="muted small">{help}</p>
    </Dialog>
  );
}
