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

// Requirements form (sequencer README, section 10.3): the sources file (`requirements.yml`: its sources, each with its
// backend, options and credentials, and the default one) and the requirements of the built-in YAML backend (the
// labels of the nodes, and each requirement with its path in the tree).
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Combo, Field, GrowText, helpText, IdText, KeyValues, scalar, Section, Tags, Text, type Choice } from "../fields";
import { send } from "../api";
import { classes, joinPath, useRoute } from "../util";
import { clone, useSession, type Doc } from "./state";

const SOURCES_FILE = "requirements.yml";
const STATUSES: Choice[] = [
  { value: "draft", description: "being written" },
  { value: "approved", description: "agreed" },
  { value: "deleted", description: "a link to it is a problem; left out of the coverage" },
  { value: "rejected", description: "a link to it is a problem; left out of the coverage" },
];
const REQ_ID = /^[A-Za-z0-9][A-Za-z0-9_.\-/]*$/;

type Data = Record<string, any>;

/** Drop the empty values of a mapping, as the YAML is written by hand. */
function pruned(obj: Data): Data {
  const out: Data = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)
      || (typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length)) continue;
    out[k] = v;
  }
  return out;
}

/** Rename a key of a mapping, keeping its place. */
function renamed(obj: Data, from: string, to: string): Data {
  return Object.fromEntries(Object.entries(obj).map(([k, v]) => [k === from ? to : k, v]));
}

export function RequirementsForm({ doc, api }: { doc: Doc; api: { setData: (d: any) => void } }) {
  const { schemas } = useSession();
  const data: Data = doc.data || {};
  const sourcesFile = doc.path.split("/").pop() === SOURCES_FILE;
  const hasSources = data.sources !== undefined && data.sources !== null;
  const setTop = (k: string, v: any) => {
    const next = clone(data);
    if (v === undefined || v === "" || (typeof v === "object" && v !== null && !Array.isArray(v) && !Object.keys(v).length)) delete next[k];
    else next[k] = v;
    api.setData(next);
  };
  const help = (path: (string | number)[]) => helpText(schemas.requirements, path);
  const showRequirements = !hasSources || data.requirements !== undefined || !sourcesFile;
  return (
    <div className="form pad">
      {sourcesFile && hasSources && (
        <Section title={`Classes (${Object.keys(data.classes || {}).length})`} help={help(["classes"])} collapsed={!Object.keys(data.classes || {}).length}>
          <Classes data={data} doc={doc} setAll={api.setData} help={help} />
        </Section>
      )}
      {sourcesFile && (
        <Section title="Sources" help={help(["sources"])}
          actions={!hasSources && (
            <button className="small" title="Keep the requirements of this file as the source 'reqs', and add sources read by other backends"
              onClick={() => {                  // the label goes with the source
                const next = clone(data);
                next.sources = { reqs: { backend: "yaml", ...(data.label ? { label: data.label } : {}) } };
                delete next.label;
                api.setData(next);
              }}>Use several sources…</button>
          )}>
          {hasSources ? (
            <Sources data={data} doc={doc} setTop={setTop} setAll={api.setData} help={help} />
          ) : (
            <p className="muted">
              This file holds the requirements themselves: it is the one source <code>reqs</code> of the built-in YAML backend, and
              test definitions name them by id (<code>reqs: [REQ-12]</code>). To read requirements from another tool too (a plugin's
              backend), use several sources.
            </p>
          )}
          {!hasSources && (
            <Field label="Label" help={help(["label"])}>
              <Text value={data.label} placeholder="reqs" onChange={(v) => setTop("label", v.trim() || undefined)} />
            </Field>
          )}
        </Section>
      )}
      {showRequirements && <Requirements data={data} setTop={setTop} help={help} />}
    </div>
  );
}

// ── sources ────────────────────────────────────────────────────────────

function Sources({ data, doc, setTop, setAll, help }: {
  data: Data; doc: Doc; setTop: (k: string, v: any) => void; setAll: (d: Data) => void; help: (p: (string | number)[]) => string | undefined;
}) {
  const { ctx } = useSession();
  const sources: Data = data.sources || {};
  const names = Object.keys(sources);
  const backends: any[] = ctx.req_backends || [];
  const backendChoices: Choice[] = backends.map((b) => ({
    value: b.name, description: [b.description, b.capabilities?.length ? `capabilities: ${b.capabilities.join(", ")}` : ""].filter(Boolean).join(" · "),
  }));
  const classDefs: Data = data.classes || {};
  const classChoices: Choice[] = Object.entries<any>(classDefs).map(([c, d]) => ({
    value: `class:${c}`, description: [`class (backend ${d?.backend || "?"})`, d?.description || d?.label].filter(Boolean).join(": "),
  }));
  const defaultName = data.default || names[0];
  const [name, setName] = useState("");
  const setSource = (n: string, spec: Data | undefined) => {
    const next = clone(sources);
    if (spec === undefined) delete next[n];
    else next[n] = pruned(spec);
    setTop("sources", next);
  };
  const bad = name && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? "letters, digits and _ (the prefix of its references: name:ID)"
    : name in sources ? "already a source" : null;
  return (
    <div className="req-sources">
      {names.length === 0 && <p className="muted">No source yet: the test definitions cannot name any requirement.</p>}
      {names.map((n) => (
        <SourceCard key={n} name={n} spec={sources[n] || {}} doc={doc} isDefault={n === defaultName} taken={names}
          backends={backends} backendChoices={[...classChoices, ...backendChoices]} help={help} classDefs={classDefs}
          onChange={(spec) => setSource(n, spec)}
          onRename={(to) => {
            const next = clone(data);
            next.sources = renamed(sources, n, to);
            if (next.default === n) next.default = to;
            setAll(next);
          }}
          onDefault={() => setTop("default", n === names[0] ? undefined : n)}
          onMakeClass={(c) => {                          // the source's definition becomes the class; it keeps its values
            const spec: Data = sources[n] || {};
            const decls: Data = {};
            const values: Data = {};
            for (const [k, v] of Object.entries<any>(spec.vars || {})) {
              const obj = v && typeof v === "object" && !Array.isArray(v);
              decls[k] = obj && v.mandatory ? { mandatory: true } : {};
              const value = obj ? v.value : v;
              if (value !== undefined && value !== null && value !== "") values[k] = value;
            }
            const all = clone(data);
            all.classes = { ...(data.classes || {}), [c]: pruned({ backend: spec.backend, label: spec.label, options: spec.options,
              credentials: spec.credentials, vars: Object.fromEntries(Object.entries(decls).map(([k, d]) => [k, Object.keys(d).length ? d : { mandatory: false }])) }) };
            all.sources = { ...all.sources, [n]: pruned({ class: c, label: spec.label, vars: values }) };
            setAll(all);
          }}
          onRemove={() => {
            const next = clone(sources);
            delete next[n];
            setTop("sources", next);
          }} />
      ))}
      <div className="req-toolbar">
        <input className="mono" placeholder="new source (jama, polarion…)" value={name} onChange={(e) => setName(e.target.value.trim())}
          onKeyDown={(e) => e.key === "Enter" && name && !bad && (setSource(name, { backend: backends.find((b) => b.name !== "yaml")?.name || "yaml" }), setName(""))} />
        <button className="small" disabled={!name || !!bad} onClick={() => { setSource(name, { backend: backends.find((b) => b.name !== "yaml")?.name || "yaml" }); setName(""); }}>Add source</button>
        {bad && <span className="bad-text small">{bad}</span>}
      </div>
      <p className="muted small">
        Test definitions and checks name a requirement of the default source by its id (<code>REQ-12</code>), and one of another
        source with its name (<code>jama:SYS-4</code>). Backends available here: {backends.map((b) => b.name).join(", ") || "none"}
        {" "}(a plugin adds one with <code>register_req_backend</code>).
      </p>
    </div>
  );
}

function SourceCard({ name, spec, doc, isDefault, taken, backends, backendChoices, help, classDefs, onChange, onRename, onDefault, onRemove, onMakeClass }: {
  name: string; spec: Data; doc: Doc; isDefault: boolean; taken: string[]; backends: any[]; backendChoices: Choice[];
  help: (p: (string | number)[]) => string | undefined; classDefs: Data;
  onChange: (spec: Data) => void; onRename: (to: string) => void; onDefault: () => void; onRemove: () => void;
  onMakeClass: (cls: string) => void;
}) {
  const { sid, files, open } = useSession();
  const [making, setMaking] = useState<string | null>(null);   // the name of the class being made from this source
  const makeBad = making === null ? null : !/^[A-Za-z_][A-Za-z0-9_-]*$/.test(making) ? "letters, digits, _ and -"
    : making in classDefs ? "already a class" : null;
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<any>(null);
  // a test of the source as in the form (saved or not): every call, or those up to a stage (step:N, list)
  const runTest: RunTest = async (upto, debug) => {
    try {
      return await send("POST", `edit/sessions/${sid}/requirements/test`, {
        path: doc.path, source: name, spec, ...(Object.keys(classDefs).length ? { classes: classDefs } : {}), ...(upto ? { upto } : {}), ...(debug || {}),
      });
    } catch (e: any) {
      return { ok: false, error: String(e?.message || e) };
    }
  };
  const test = async () => {
    setTesting(true);
    setResult(null);                                         // the values of the last test go: no mixing them up
    try {
      setResult(await runTest());
    } finally {
      setTesting(false);
    }
  };
  const ofClass = spec.class ? String(spec.class) : null;
  const backend = backends.find((b) => b.name === String((ofClass ? classDefs[ofClass]?.backend : spec.backend) || "").toLowerCase());
  const pickBackend = (v: string) => {
    if (v.startsWith("class:")) {                       // a class: the source keeps only its label and the values
      const c = v.slice(6);
      const kept: Data = ofClass === c ? spec.vars || {} : {};
      onChange(pruned({ class: c, label: spec.label, vars: kept }));
    } else onChange(ofClass ? pruned({ backend: v, label: spec.label }) : { ...spec, backend: v });
  };
  // the source the page was opened on (#/edit/<sid>?file=requirements.yml&source=<name>), from the Requirements view
  const target = useRoute().query.get("source") === name;
  const card = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (target) card.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [target]);
  const options: Data = spec.options || {};
  const credentials: Data = spec.credentials || {};
  const set = (k: string, v: any) => onChange({ ...spec, [k]: v });
  const knownCredentials: string[] = Object.keys(backend?.credentials || {});
  // the file a yaml source reads, in the session
  const yamlFile = backend?.name === "yaml" ? joinPath(doc.path, String(options.file || SOURCES_FILE)) : null;
  const yamlFound = yamlFile && files.some((f) => f.path === yamlFile);
  const credRows = [...new Set([...Object.keys(credentials), ...knownCredentials])];
  return (
    <div ref={card} className={classes("req-source", isDefault && "default", target && "target")}>
      <div className="req-source-head">
        <IdText value={name} taken={taken} onChange={onRename} />
        <label className="toggle" title="The source of the references without a prefix">
          <input type="radio" checked={isDefault} onChange={onDefault} /> default
        </label>
        <span className="grow" />
        {!ofClass && making === null && (
          <button className="small" onClick={() => setMaking(classDefs[name] ? `${name}_class` : name)}
            title="Turn this source's definition into a class, to use it for other subsystems too">Make a class…</button>
        )}
        <button className="small" disabled={testing} onClick={test}
          title="Read this source as datapack generation does, as in this form (saved or not): its credentials are read from the environment of the sequencer host">
          {testing ? "Testing…" : "Test connection"}</button>
        <button className="icon" title="Remove this source" onClick={onRemove}>×</button>
      </div>
      {making !== null && (
        <div className="req-detach-confirm small">
          <p><strong>Make a class of this source?</strong> Its backend, options, credentials and label move to a new class; its variables
            become the class variables (mandatory ones stay mandatory), and this source becomes a source of that class, keeping its
            label and its variable values. It reads the same requirements as before. Other sources can then pick the class in their
            Backend list and give their own values (another subsystem). To make a value differ per source, turn it into a variable
            first (<code>{"{{ name }}"}</code> in the options). (Undo brings it back.)</p>
          <label>Class name <input className={classes("mono", makeBad && "invalid")} value={making} autoFocus
            onChange={(e) => setMaking(e.target.value.trim())}
            onKeyDown={(e) => { if (e.key === "Enter" && !makeBad && making) { onMakeClass(making); setMaking(null); } if (e.key === "Escape") setMaking(null); }} /></label>
          {makeBad && <span className="bad-text"> {makeBad}</span>}{" "}
          <button className="small" disabled={!making || !!makeBad} onClick={() => { onMakeClass(making!); setMaking(null); }}>Make the class</button>{" "}
          <button className="link small" onClick={() => setMaking(null)}>cancel</button>
        </div>
      )}
      {result && (
        <div className={classes("req-test", result.ok ? "ok" : "bad")}>
          {result.ok ? (
            <>
              <div><strong>{result.count}</strong> requirement(s), {result.nodes} node(s), read in {result.seconds} s{result.count > result.sample.length ? `; the first ${result.sample.length}:` : ":"}</div>
              {result.vars && Object.keys(result.vars).length > 0 && (
                <div className="req-vars small">Variables: {Object.entries<any>(result.vars).map(([k, v]) => (
                  <span key={k} className={classes("mono", k in (result.context || {}) && "strong")} title={k in (result.context || {}) ? "mandatory: the context of the requirements" : undefined}>
                    {k}={typeof v === "object" ? JSON.stringify(v) : String(v) || "∅"}</span>
                ))}</div>
              )}
              {result.sample.length > 0 && (
                <table className="grid compact">
                  <thead><tr><th>Id</th><th>Path</th><th>Title</th><th>Status</th><th>Version</th></tr></thead>
                  <tbody>{result.sample.map((r: any) => (
                    <tr key={r.id}><td><code>{r.id}</code></td><td className="small">{r.path}</td><td>{r.title}</td><td>{r.status}</td><td className="mono small">{r.version}</td></tr>
                  ))}</tbody>
                </table>
              )}
            </>
          ) : <div className="error-text">{result.error}</div>}
          <Calls result={result} />
          <button className="link small" onClick={() => setResult(null)}>hide</button>
        </div>
      )}
      <Field label="Backend" help={ofClass ? help(["sources", name, "class"]) : help(["sources", name, "backend"])}
        error={ofClass ? (classDefs[ofClass] ? null : `no class '${ofClass}' in this file (Classes, above)`)
          : spec.backend && !backend ? `no backend '${spec.backend}' here: a plugin registers it` : null}>
        <Combo mono value={ofClass ? `class:${ofClass}` : spec.backend} onChange={pickBackend} choices={backendChoices} />
      </Field>
      {ofClass && <ClassSourceForm name={name} cls={ofClass} def={classDefs[ofClass]} spec={spec} help={help} onChange={onChange} />}
      {!ofClass && <>
      <Field label="Label" help={help(["sources", name, "label"])}><Text value={spec.label} onChange={(v) => set("label", v)} placeholder={name} /></Field>
      <Field label="Variables" help={help(["sources", name, "vars"])}>
        <Variables value={spec.vars} onChange={(v) => set("vars", Object.keys(v).length ? v : undefined)} />
      </Field>
      <Field label="Options" help={help(["sources", name, "options"])} wide
        hint={yamlFile && (yamlFound
          ? <button className="link small" onClick={() => open(yamlFile)}>open {yamlFile}</button>
          : <span className="bad-text small">no file {yamlFile} in the session</span>)}>
        <BackendOptions backend={backend} options={options} onChange={(v) => set("options", v)}
          credentials={credentials} onCredentials={(v) => set("credentials", v)} runTest={runTest} />
      </Field>
      {backend?.name !== "rest" && (credRows.length > 0 || backend?.name !== "yaml") && (
        <Field label="Credentials" help={help(["sources", name, "credentials"])} wide>
          <Credentials value={credentials} rows={credRows} help={backend?.credentials} onChange={(v) => set("credentials", v)} />
        </Field>
      )}
      </>}
    </div>
  );
}

/** A source of a class: its label and the values of the class variables, nothing else; Detach makes it a source of
 * its own, a copy of the class. */
function ClassSourceForm({ name, cls, def, spec, help, onChange }: {
  name: string; cls: string; def?: Data; spec: Data; help: (p: (string | number)[]) => string | undefined; onChange: (spec: Data) => void;
}) {
  const decls: Data = def?.vars || {};
  const given: Data = spec.vars || {};
  const setVar = (k: string, v: string) => onChange(pruned({ ...spec, vars: put(given, k, scalar(v)) }));
  const [confirm, setConfirm] = useState(false);
  const detach = () => {
    const vars: Data = {};
    for (const [k, d] of Object.entries<any>(decls)) {
      const { value, mandatory } = declOf(d);
      const v = given[k] !== undefined ? given[k] : value;
      vars[k] = mandatory ? { value: v ?? "", mandatory: true } : v ?? "";
    }
    onChange(pruned({ backend: def?.backend, label: spec.label || def?.label, options: clone(def?.options || {}),
      credentials: clone(def?.credentials || {}), vars }));
  };
  const unknown = Object.keys(given).filter((k) => !(k in decls));
  // a variable the class makes optional (not mandatory, with a default) is listed only when given or picked
  const [picked, setPicked] = useState<string[]>([]);
  const optional = (k: string) => { const d = declOf(decls[k]); return !d.mandatory && d.value !== undefined && d.value !== null && d.value !== ""; };
  const listed = Object.keys(decls).filter((k) => !optional(k) || given[k] !== undefined || picked.includes(k));
  const hidden = Object.keys(decls).filter((k) => !listed.includes(k));
  return (
    <>
      <Field label="Label" help={help(["sources", name, "label"])}><Text value={spec.label} onChange={(v) => onChange(pruned({ ...spec, label: v }))} placeholder={def?.label || name} /></Field>
      <Field label="Class variables" help={`The values of the variables of class '${cls}' for this source. ${help(["sources", name, "vars"]) || ""}`} wide>
        {Object.keys(decls).length === 0 ? <span className="muted small">Class '{cls}' has no variables: this source is the class as it is.</span> : listed.length > 0 && (
          <table className="grid compact req-class-values">
            <tbody>{listed.map((k) => [k, decls[k]] as [string, any]).map(([k, d]) => {
              const { value, mandatory, help: what } = declOf(d);
              const missing = mandatory && (given[k] === undefined || given[k] === "") && (value === undefined || value === "");
              return (
                <tr key={k}>
                  <td><span className="mono">{k}</span>{mandatory && <span className="bad-text" title="mandatory: the context of the requirements"> *</span>}
                    {what && <div className="muted small">{what}</div>}</td>
                  <td><Text mono value={given[k] === undefined ? "" : String(given[k])}
                    placeholder={value !== undefined && value !== null && value !== "" ? `optional: ${value} (the class's default)` : mandatory ? "required" : "optional: empty if not given"}
                    onChange={(v) => setVar(k, v)} />{missing && <div className="bad-text small">a value is required</div>}</td>
                <td>{optional(k) && <button className="icon" title="Use the class's default"
                  onClick={() => { setPicked(picked.filter((x) => x !== k)); onChange(pruned({ ...spec, vars: put(given, k, undefined) })); }}>×</button>}</td>
                </tr>
              );
            })}</tbody>
          </table>
        )}
        {hidden.length > 0 && (
          <div className="small">
            <select value="" onChange={(e) => e.target.value && setPicked([...picked, e.target.value])}
              title="Variables with a default in the class: this source uses the defaults unless you give a value">
              <option value="">Set another variable ({hidden.length} with a default)…</option>
              {hidden.map((k) => { const d = declOf(decls[k]); return <option key={k} value={k}>{k} = {String(d.value)}{d.help ? `: ${d.help}` : ""}</option>; })}
            </select>
          </div>
        )}
        {unknown.length > 0 && <div className="bad-text small">Not variables of class '{cls}': {unknown.join(", ")}
          {" "}<button className="link small" onClick={() => onChange(pruned({ ...spec, vars: Object.fromEntries(Object.entries(given).filter(([k]) => k in decls)) }))}>remove them</button></div>}
      </Field>
      <div className="req-detach small">
        <span className="muted">Backend, options and credentials come from class <code>{cls}</code>{def?.backend ? <> (backend <code>{def.backend}</code>)</> : null}: a change to the class reaches every source of it.</span>{" "}
        {!confirm ? <button className="link small" onClick={() => setConfirm(true)}>Detach…</button> : (
          <div className="req-detach-confirm">
            <p><strong>Detach from class {cls}?</strong> This source becomes a full copy of the class (backend, options, credentials), with
              its variables set to the values above. You can then change anything in it, but it no longer follows the class: a later fix
              to class <code>{cls}</code> will not reach it. The class and its other sources are not changed. (Undo brings it back.)</p>
            <button className="small" onClick={() => { setConfirm(false); detach(); }}>Detach</button>{" "}
            <button className="link small" onClick={() => setConfirm(false)}>cancel</button>
          </div>
        )}
      </div>
    </>
  );
}

/** A class variable as declared: a default value, or {mandatory, value, help}. */
function declOf(d: any): { value: any; mandatory: boolean; help?: string } {
  if (d && typeof d === "object" && !Array.isArray(d)) return { value: d.value, mandatory: !!d.mandatory, help: d.help };
  return { value: d, mandatory: false };
}

// ── classes ────────────────────────────────────────────────────────────

function Classes({ data, doc, setAll, help }: {
  data: Data; doc: Doc; setAll: (d: Data) => void; help: (p: (string | number)[]) => string | undefined;
}) {
  const { ctx } = useSession();
  const defs: Data = data.classes || {};
  const names = Object.keys(defs);
  const backends: any[] = ctx.req_backends || [];
  const backendChoices: Choice[] = backends.map((b) => ({ value: b.name, description: b.description }));
  const [name, setName] = useState("");
  const users = (c: string) => Object.entries<any>(data.sources || {}).filter(([, s]) => s?.class === c).map(([n]) => n);
  const setClasses = (next: Data) => {
    const all = clone(data);
    if (Object.keys(next).length) all.classes = next; else delete all.classes;
    setAll(all);
  };
  const bad = name && !/^[A-Za-z_][A-Za-z0-9_-]*$/.test(name) ? "letters, digits, _ and -" : name in defs ? "already a class" : null;
  const add = () => { if (!name || bad) return; setClasses({ ...defs, [name]: { backend: backends.find((b) => b.name === "rest") ? "rest" : backends[0]?.name || "" } }); setName(""); };
  return (
    <div className="req-sources">
      <p className="muted small">
        A class is a source written once (backend, options, credentials) with <em>class variables</em>: each source of it only gives their
        values (the same tool, for another subsystem). Use <code>{"{{ name }}"}</code> in the options and credentials, <code>$name</code> in jq.
        A source picks a class in its Backend list.
      </p>
      {names.map((c) => (
        <ClassCard key={c} name={c} def={defs[c] || {}} doc={doc} taken={names} users={users(c)} backends={backends} backendChoices={backendChoices}
          help={help}
          onChange={(d) => setClasses({ ...defs, [c]: pruned(d) })}
          onRename={(to) => {
            const all = clone(data);
            all.classes = renamed(defs, c, to);
            for (const s of Object.values<any>(all.sources || {})) if (s?.class === c) s.class = to;
            setAll(all);
          }}
          onRemove={() => { const next = clone(defs); delete next[c]; setClasses(next); }} />
      ))}
      <div className="req-toolbar">
        <input className="mono" placeholder="new class (alm, jama…)" value={name} onChange={(e) => setName(e.target.value.trim())}
          onKeyDown={(e) => e.key === "Enter" && add()} />
        <button className="small" disabled={!name || !!bad} onClick={add}>Add class</button>
        {bad && <span className="bad-text small">{bad}</span>}
      </div>
    </div>
  );
}

function ClassCard({ name, def, doc, taken, users, backends, backendChoices, help, onChange, onRename, onRemove }: {
  name: string; def: Data; doc: Doc; taken: string[]; users: string[]; backends: any[]; backendChoices: Choice[];
  help: (p: (string | number)[]) => string | undefined; onChange: (d: Data) => void; onRename: (to: string) => void; onRemove: () => void;
}) {
  const { sid } = useSession();
  const decls: Data = def.vars || {};
  const ask = useTestValues(doc.path, name, decls);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<any>(null);
  // a test of the class as in the form: with values for its variables, asked at each test (remembered in this browser)
  const runTest: RunTest = async (upto, debug) => {
    const values = await ask.ask();
    if (values === null) return { ok: false, error: "Test cancelled: no values given for the class variables." };
    try {
      return await send("POST", `edit/sessions/${sid}/requirements/test`, {
        path: doc.path, source: "", class: name, spec: def, values, ...(upto ? { upto } : {}), ...(debug || {}),
      });
    } catch (e: any) {
      return { ok: false, error: String(e?.message || e) };
    }
  };
  const test = async () => {
    setTesting(true);
    setResult(null);
    try { setResult(await runTest()); } finally { setTesting(false); }
  };
  const backend = backends.find((b) => b.name === String(def.backend || "").toLowerCase());
  const options: Data = def.options || {};
  const credentials: Data = def.credentials || {};
  const set = (k: string, v: any) => onChange({ ...def, [k]: v });
  const credRows = [...new Set([...Object.keys(credentials), ...Object.keys(backend?.credentials || {})])];
  return (
    <div className="req-source req-class">
      <div className="req-source-head">
        <span className="req-class-tag">class</span>
        <IdText value={name} taken={taken} onChange={onRename} />
        <span className="muted small">{users.length ? `used by ${users.join(", ")}` : "used by no source yet"}</span>
        <span className="grow" />
        <button className="small" disabled={testing} onClick={test}
          title="Read the class as a source would, with values for its variables (asked now): the credentials are read from the environment of the sequencer host">
          {testing ? "Testing…" : "Test connection"}</button>
        <button className="icon" title={users.length ? `Used by ${users.join(", ")}: detach or remove them first` : "Remove this class"}
          disabled={users.length > 0} onClick={onRemove}>×</button>
      </div>
      {ask.element}
      {users.length > 1 && <div className="hint small">A change here reaches {users.length} sources ({users.join(", ")}).</div>}
      {result && (
        <div className={classes("req-test", result.ok ? "ok" : "bad")}>
          {result.ok ? (
            <>
              <div><strong>{result.count}</strong> requirement(s), {result.nodes} node(s), read in {result.seconds} s</div>
              {result.vars && Object.keys(result.vars).length > 0 && (
                <div className="req-vars small">Variables: {Object.entries<any>(result.vars).map(([k, v]) => <VarValue key={k} name={k} value={v} />)}</div>
              )}
              {result.sample?.length > 0 && (
                <table className="grid compact">
                  <thead><tr><th>Id</th><th>Path</th><th>Title</th><th>Status</th></tr></thead>
                  <tbody>{result.sample.map((r: any) => (
                    <tr key={r.id}><td><code>{r.id}</code></td><td className="small">{r.path}</td><td>{r.title}</td><td>{r.status}</td></tr>
                  ))}</tbody>
                </table>
              )}
            </>
          ) : <div className="error-text">{result.error}</div>}
          <Calls result={result} />
          <button className="link small" onClick={() => setResult(null)}>hide</button>
        </div>
      )}
      <Field label="Backend" help={help(["classes", name, "backend"])}
        error={def.backend && !backend ? `no backend '${def.backend}' here: a plugin registers it` : null}>
        <Combo mono value={def.backend} onChange={(v) => set("backend", v)} choices={backendChoices} />
      </Field>
      <Field label="Label" help={help(["classes", name, "label"])}><Text value={def.label} onChange={(v) => set("label", v)} placeholder="the source's name" /></Field>
      <Field label="Description" help={help(["classes", name, "description"])}><Text value={def.description} onChange={(v) => set("description", v)} placeholder="what it reads" /></Field>
      <Field label="Class variables" help={help(["classes", name, "vars"])} wide>
        <ClassVars value={decls} onChange={(v) => set("vars", Object.keys(v).length ? v : undefined)} />
      </Field>
      <Field label="Options" help={help(["classes", name, "options"])} wide>
        <BackendOptions backend={backend} options={options} onChange={(v) => set("options", v)}
          credentials={credentials} onCredentials={(v) => set("credentials", v)} runTest={runTest} />
      </Field>
      {backend?.name !== "rest" && (credRows.length > 0 || backend?.name !== "yaml") && (
        <Field label="Credentials" help={help(["classes", name, "credentials"])} wide>
          <Credentials value={credentials} rows={credRows} help={backend?.credentials} onChange={(v) => set("credentials", v)} />
        </Field>
      )}
    </div>
  );
}

/** The class variables: name, mandatory, default, help. */
function ClassVars({ value, onChange }: { value: Data; onChange: (v: Data) => void }) {
  const [name, setName] = useState("");
  const names = Object.keys(value);
  const write = (k: string, d: { value?: any; mandatory?: boolean; help?: string }) => {
    const decl = pruned({ value: d.value, mandatory: d.mandatory || undefined, help: d.help });
    onChange({ ...value, [k]: Object.keys(decl).length === 1 && "value" in decl ? decl.value : Object.keys(decl).length ? decl : { mandatory: false } });
  };
  const bad = name && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? "letters, digits and _" : name in value ? "already a variable" : null;
  const add = () => { if (!name || bad) return; onChange({ ...value, [name]: { mandatory: true } }); setName(""); };
  return (
    <div>
      {names.length > 0 && (
        <table className="grid compact req-class-vars">
          <thead><tr><th>Name</th><th title="Each source must give it a value; it is the context of its requirements">Mandatory</th><th>Default</th><th>Help</th><th /></tr></thead>
          <tbody>{names.map((k) => {
            const d = declOf(value[k]);
            return (
              <tr key={k}>
                <td><IdText value={k} taken={names} onChange={(to) => onChange(renamed(value, k, to))} /></td>
                <td><input type="checkbox" checked={d.mandatory} onChange={(e) => write(k, { ...d, mandatory: e.target.checked })} /></td>
                <td><Text mono value={d.value === undefined || d.value === null ? "" : String(d.value)} placeholder="none"
                  onChange={(v) => write(k, { ...d, value: v === "" ? undefined : scalar(v) })} /></td>
                <td><Text value={d.help} placeholder="what it is" onChange={(v) => write(k, { ...d, help: v || undefined })} /></td>
                <td><button className="icon" title="Remove this variable" onClick={() => { const next = { ...value }; delete next[k]; onChange(next); }}>×</button></td>
              </tr>
            );
          })}</tbody>
        </table>
      )}
      <div className="req-toolbar">
        <input className="mono small" placeholder="new class variable (subsystem…)" value={name} onChange={(e) => setName(e.target.value.trim())}
          onKeyDown={(e) => e.key === "Enter" && add()} />
        <button className="small" disabled={!name || !!bad} onClick={add}>Add</button>
        {bad && <span className="bad-text small">{bad}</span>}
      </div>
      <div className="hint">Declared here, given by each source of the class. A test of the class asks for their values each time
        (remembered in this browser, never saved in the file).</div>
    </div>
  );
}

/** The values of the class variables for a test, asked at each test in a small form (the last ones remembered in
 * this browser). ask(): the values, or null when cancelled; element: the form, while asking. */
function useTestValues(path: string, cls: string, decls: Data) {
  const key = `tsq.req-class-test:${path}:${cls}`;
  const [asking, setAsking] = useState<{ resolve: (v: Data | null) => void } | null>(null);
  const [values, setValues] = useState<Data>(() => {
    try { return JSON.parse(localStorage.getItem(key) || "{}") || {}; } catch { return {}; }
  });
  const names = Object.keys(decls);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { if (asking) box.current?.scrollIntoView({ block: "center", behavior: "smooth" }); }, [asking]);
  const ask = (): Promise<Data | null> => names.length === 0 ? Promise.resolve({})
    : new Promise((resolve) => setAsking({ resolve }));
  const done = (ok: boolean) => {
    if (!asking) return;
    if (ok) try { localStorage.setItem(key, JSON.stringify(values)); } catch { /* not kept: fine */ }
    asking.resolve(ok ? Object.fromEntries(names.map((k) => [k, values[k] ?? ""])) : null);
    setAsking(null);
  };
  const element = asking && (
    <div ref={box} className="req-test-values" onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); done(true); } if (e.key === "Escape") done(false); }}>
      <div className="small"><strong>Test values</strong> <span className="muted">for the class variables, for this test only (not saved in the file)</span></div>
      <table className="grid compact"><tbody>{names.map((k, i) => {
        const d = declOf(decls[k]);
        return (
          <tr key={k}>
            <td className="small"><span className="mono">{k}</span>{d.mandatory && <span className="bad-text"> *</span>}{d.help && <div className="muted small">{d.help}</div>}</td>
            <td><input className="mono" autoFocus={i === 0} value={values[k] === undefined ? "" : String(values[k])}
              placeholder={d.value !== undefined && d.value !== null && d.value !== "" ? `${d.value} (default)` : d.mandatory ? "required" : ""}
              onChange={(e) => setValues({ ...values, [k]: e.target.value })} /></td>
          </tr>
        );
      })}</tbody></table>
      <button className="small primary" onClick={() => done(true)}>Run the test</button>{" "}
      <button className="link small" onClick={() => done(false)}>cancel</button>
    </div>
  );
  return { ask, element };
}

// ── requirements of the YAML backend ───────────────────────────────────

function Requirements({ data, setTop, help }: {
  data: Data; setTop: (k: string, v: any) => void; help: (p: (string | number)[]) => string | undefined;
}) {
  const reqs: Data = data.requirements || {};
  const nodes: Data = data.nodes || {};
  const ids = Object.keys(reqs);
  const [filter, setFilter] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [newId, setNewId] = useState("");
  const [newNode, setNewNode] = useState("");
  const addNode = () => {
    if (!newNode || paths.includes(newNode)) return;
    setTop("nodes", { ...nodes, [newNode]: newNode.split("/").pop() });
    setNewNode("");
  };
  // every node of the tree: labelled ones, and the prefixes of the paths of the requirements
  const paths = useMemo(() => {
    const all = new Set<string>(Object.keys(nodes).map((p) => p.replace(/^\/+|\/+$/g, "")));
    for (const r of Object.values<any>(reqs)) {
      const parts = String(r?.path || "").split("/").filter(Boolean);
      for (let i = 1; i <= parts.length; i++) all.add(parts.slice(0, i).join("/"));
    }
    return [...all].filter(Boolean).sort();
  }, [nodes, reqs]);
  const count = (p: string) => Object.values<any>(reqs).filter((r) => { const rp = String(r?.path || ""); return rp === p || rp.startsWith(p + "/"); }).length;
  const pathChoices: Choice[] = paths.map((p) => ({ value: p, description: nodes[p] }));
  const setReq = (id: string, body: Data | undefined) => {
    const next = clone(reqs);
    if (body === undefined) delete next[id];
    else next[id] = pruned(body);
    setTop("requirements", next);
  };
  const f = filter.trim().toLowerCase();
  const shown = ids.filter((id) => {
    if (!f) return true;
    const r = reqs[id] || {};
    return [id, r.path, r.title, r.description, ...(r.tags || [])].some((x) => String(x || "").toLowerCase().includes(f));
  });
  const idBad = newId && !REQ_ID.test(newId) ? "letters, digits, and _ . - /" : newId in reqs ? "already a requirement" : null;
  const add = () => {
    if (!newId || idBad) return;
    const last = ids.length ? reqs[ids[ids.length - 1]] : null;
    const path = paths.includes(filter.trim()) ? filter.trim() : last?.path || "";
    setTop("requirements", { ...reqs, [newId]: pruned({ path, title: "", description: "" }) });
    setOpenId(newId);
    setNewId("");
  };
  return (
    <>
      <Section title="Tree" collapsed={!Object.keys(nodes).length} help={help(["nodes"])}>
        <p className="muted small">
          The nodes come from the paths of the requirements (a node added here with no requirement yet keeps its label); a label shows beside its node in the Requirements tab and the reports.
        </p>
        <table className="grid compact req-nodes">
          <thead><tr><th>Node</th><th>Label</th><th>Requirements</th><th /></tr></thead>
          <tbody>
            {paths.map((p) => (
              <tr key={p}>
                <td><code style={{ paddingLeft: `${(p.split("/").length - 1) * 14}px` }}>{p}</code></td>
                <td><Text value={nodes[p]} placeholder="no label" onChange={(v) => setTop("nodes", pruned({ ...nodes, [p]: v }))} /></td>
                <td className="muted small">{count(p)}</td>
                <td>{p in nodes && <button className="icon" title="Remove its label" onClick={() => { const next = { ...nodes }; delete next[p]; setTop("nodes", next); }}>×</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="req-toolbar">
          <input className="mono" placeholder="new node (SYS/BRAKE)" value={newNode} onChange={(e) => setNewNode(e.target.value.trim().replace(/^\/+|\/+$/g, ""))}
            onKeyDown={(e) => e.key === "Enter" && addNode()} />
          <button className="small" disabled={!newNode || paths.includes(newNode)} onClick={addNode}>Add node</button>
        </div>
      </Section>
      <Section title={`Requirements (${ids.length})`} help={help(["requirements"])}>
        <div className="req-toolbar">
          <input placeholder="filter: id, path, title, tag…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <span className="grow" />
          <input className="mono" placeholder="new requirement id (REQ-13)" value={newId} onChange={(e) => setNewId(e.target.value.trim())}
            onKeyDown={(e) => e.key === "Enter" && add()} />
          <button className="small primary" disabled={!newId || !!idBad} onClick={add}>Add requirement</button>
          {idBad && <span className="bad-text small">{idBad}</span>}
        </div>
        {ids.length === 0 ? (
          <p className="muted">No requirement yet.</p>
        ) : (
          <div className="scroll">
            <table className="grid compact req-edit">
              <colgroup><col className="c-fold" /><col className="c-id" /><col className="c-path" /><col /><col className="c-status" /><col className="c-x" /></colgroup>
              <thead><tr><th /><th>Id</th><th>Path</th><th>Title</th><th>Status</th><th /></tr></thead>
              <tbody>
                {shown.map((id) => (
                  <RequirementRow key={id} id={id} body={reqs[id] || {}} taken={ids} open={openId === id} pathChoices={pathChoices} help={help}
                    onToggle={() => setOpenId(openId === id ? null : id)}
                    onChange={(b) => setReq(id, b)}
                    onRename={(to) => { setTop("requirements", renamed(reqs, id, to)); if (openId === id) setOpenId(to); }}
                    onRemove={() => setReq(id, undefined)} />
                ))}
                {shown.length === 0 && <tr><td colSpan={6} className="muted">No requirement matches the filter.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted small">
          A requirement's version is a hash of its content unless written: a test that verified it before a change shows <em>changed since</em>.
          Renaming a requirement does not rename the references to it: the test definitions that name it then show a problem.
        </p>
      </Section>
    </>
  );
}

function RequirementRow({ id, body, taken, open, pathChoices, help, onToggle, onChange, onRename, onRemove }: {
  id: string; body: Data; taken: string[]; open: boolean; pathChoices: Choice[];
  help: (p: (string | number)[]) => string | undefined;
  onToggle: () => void; onChange: (b: Data) => void; onRename: (to: string) => void; onRemove: () => void;
}) {
  const set = (k: string, v: any) => onChange({ ...body, [k]: v });
  const inactive = ["deleted", "rejected"].includes(String(body.status || "").toLowerCase());
  const h = (k: string) => help(["requirements", id, k]);
  return (
    <>
      <tr className={classes(open && "open", inactive && "inactive")}>
        <td><button className="fold" title={open ? "Hide its text" : "Edit its text"} onClick={onToggle}>{open ? "▾" : "▸"}</button></td>
        <td><ReqIdText value={id} taken={taken} onChange={onRename} /></td>
        <td title={body.path && pathChoices.find((c) => c.value === body.path)?.description || undefined}><Combo mono value={body.path} onChange={(v) => set("path", v.replace(/^\/+|\/+$/g, ""))} choices={pathChoices} placeholder="SYS/BRAKE" /></td>
        <td><div className="req-title-cell"><Text value={body.title} onChange={(v) => set("title", v)} />
          {(body.tags || []).length > 0 && <span className="req-tag-count" title={`Tags: ${(body.tags || []).join(", ")}`}>{(body.tags || []).map(String).join(", ")}</span>}</div></td>
        <td title={STATUSES.find((c) => c.value === body.status)?.description}><Combo value={body.status} onChange={(v) => set("status", v)} choices={STATUSES} /></td>
        <td><button className="icon" title="Remove this requirement" onClick={onRemove}>×</button></td>
      </tr>
      {open && (
        <tr className="req-edit-detail">
          <td />
          <td colSpan={5}>
            <div className="form">
              <Field label="Tags" help={h("tags")}><Tags value={(body.tags || []).map(String)} onChange={(v) => set("tags", v)} /></Field>
              <Field label="Description" help={h("description")} wide><Text multiline value={body.description} onChange={(v) => set("description", v)} /></Field>
              <Field label="Rationale" help={h("rationale")} wide><Text multiline value={body.rationale} onChange={(v) => set("rationale", v)} /></Field>
              <Field label="Comments" help={h("comments")} wide><Text multiline value={body.comments} onChange={(v) => set("comments", v)} /></Field>
              <Field label="Version" help={h("version")}><Text mono value={body.version} onChange={(v) => set("version", v)} placeholder="a hash of its content" /></Field>
              <Field label="Link" help={h("url")}><Text mono value={body.url} onChange={(v) => set("url", v)} placeholder="the url option of the source" /></Field>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/** A requirement id, applied when the field is left (renaming at each letter would move the row). */
function ReqIdText({ value, taken, onChange }: { value: string; taken: string[]; onChange: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  const problem = !REQ_ID.test(draft) ? "letters, digits, and _ . - /" : draft !== value && taken.includes(draft) ? "already used" : null;
  return (
    <input className={classes("mono", problem && "invalid")} value={draft} title={problem || undefined}
      onChange={(e) => setDraft(e.target.value.trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      onBlur={() => { if (!problem && draft !== value) onChange(draft); else setDraft(value); }} />
  );
}

// ── backend options ────────────────────────────────────────────────────

/** Sets a key of an object, or removes it when the value is empty. */
function put(data: Data | undefined, k: string, v: any): Data {
  const next = { ...(data || {}) };
  if (v === undefined || v === null || v === "" || (typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0)) delete next[k];
  else next[k] = v;
  return next;
}

/** The options of a source: each option its backend knows, as a field with its help (the rest backend has its own
 * form), and the other options as free keys. */
function BackendOptions({ backend, options, onChange, credentials, onCredentials, runTest }: {
  backend: any; options: Data; onChange: (v: Data) => void; credentials: Data; onCredentials: (v: Data) => void; runTest: RunTest;
}) {
  const known: Record<string, string> = backend?.options || {};
  const names = Object.keys(known);
  const set = (k: string, v: any) => onChange(put(options, k, v));
  const others = Object.keys(options).filter((k) => !names.includes(k));
  return (
    <div className="req-options">
      {backend?.name === "rest" ? <RestOptions options={options} help={known} set={set} setOptions={onChange} credentials={credentials} onCredentials={onCredentials} credHelp={backend.credentials} runTest={runTest} />
        : names.map((k) => (
          <Field key={k} label={k} help={known[k]}><OptionValue value={options[k]} onChange={(v) => set(k, v)} /></Field>
        ))}
      {(others.length > 0 || names.length === 0) && (
        <Field label={names.length ? "Other options" : "Options"} help={names.length ? "Options this backend does not describe: a plugin may read them." : undefined}>
          <KeyValues value={options} onChange={onChange} exclude={names} nested />
        </Field>
      )}
      {names.length > 0 && others.length === 0 && (
        <AddOption onAdd={(k) => !names.includes(k) && set(k, "")} />
      )}
    </div>
  );
}

function AddOption({ onAdd }: { onAdd: (k: string) => void }) {
  const [k, setK] = useState("");
  const add = () => { if (k) onAdd(k); setK(""); };
  return (
    <div className="req-toolbar">
      <input className="mono small" placeholder="other option…" value={k} onChange={(e) => setK(e.target.value.trim())}
        onKeyDown={(e) => e.key === "Enter" && add()} />
      {k && <button className="small" onClick={add}>Add</button>}
    </div>
  );
}

/** A value of an option: a list as tags, a mapping as keys, a scalar as text (numbers and booleans as such). */
function OptionValue({ value, onChange, placeholder }: { value: any; onChange: (v: any) => void; placeholder?: string }) {
  if (Array.isArray(value)) return <Tags value={value.map(String)} onChange={onChange} />;
  if (value && typeof value === "object") return <KeyValues value={value} onChange={onChange} nested />;
  return <Text mono value={value} placeholder={placeholder} onChange={(v) => onChange(scalar(v))} />;
}

const AUTH_KINDS = [
  { value: "", label: "none" },
  { value: "bearer", label: "bearer token" },
  { value: "basic", label: "basic (user, password)" },
  { value: "header", label: "header" },
  { value: "query", label: "query parameter" },
];
const REST_FIELDS: [string, string][] = [
  ["id", "Its id, as test definitions name it (required)"],
  ["title", "Its title"],
  ["description", "Its text"],
  ["path", "Its place in the tree: nodes joined by /"],
  ["status", "draft, approved, deleted, rejected…"],
  ["tags", "A list"],
  ["version", "Its version or revision"],
  ["rationale", "Why it is required"],
  ["comments", "Comments"],
  ["url", "A link to it in its tool (default: the URL template)"],
];

/** The rest backend's options: base URL and authentication, how to list and get the requirements, and the jq
 * expression giving each field of a requirement from an item of an answer. */
function RestOptions({ options, help, set, setOptions, credentials: creds, onCredentials, credHelp, runTest }: {
  options: Data; help: Record<string, string>; set: (k: string, v: any) => void; setOptions: (v: Data) => void;
  credentials: Data; onCredentials: (v: Data) => void; credHelp?: Record<string, string>; runTest: RunTest;
}) {
  const credentials = Object.keys(creds);
  const auth: Data = options.auth && typeof options.auth === "object" ? options.auth : {};
  const kind = AUTH_KINDS.find((a) => a.value && auth[a.value] !== undefined)?.value || "";
  const credChoices: Choice[] = [...new Set([...credentials, ...Object.keys(credHelp || {})])].map((value) => ({ value, description: credHelp?.[value] || "a credential of this source" }));
  const list: Data = options.list || {};
  const get: Data = options.get || {};
  const setIn = (k: string, obj: Data, sub: string, v: any) => set(k, put(obj, sub, v));
  const basic: any[] = Array.isArray(auth.basic) ? auth.basic : auth.basic ? [auth.basic] : [];
  // the credentials the authentication names: only those are asked for (a token, or a user and a password...)
  const needed: string[] = (kind === "bearer" ? [auth.bearer] : kind === "basic" ? basic
    : kind ? Object.values(auth[kind] || {}) : []).filter(Boolean).map(String);
  const credRows = [...new Set([...needed, ...credentials])];
  const setAuth = (to: string) => set("auth", to === "" ? undefined
    : to === "bearer" ? { bearer: "token" }
    : to === "basic" ? { basic: ["user", "password"] }
    : to === "header" ? { header: { "X-Api-Key": "token" } }
    : { query: { api_key: "token" } });
  return (
    <>
      <Group title="Connection" note="the server, and how to sign in">
        <Field label="Base URL" help={help.base}>
          <Text mono value={options.base} placeholder="https://reqs.example.com/rest/v1" onChange={(v) => set("base", v.trim())} />
        </Field>
        <Field label="Authentication" help={help.auth}>
          <div className="req-auth">
            <select value={kind} onChange={(e) => setAuth(e.target.value)}>
              {AUTH_KINDS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
            {kind === "bearer" && (
              <Combo mono value={auth.bearer} choices={credChoices} placeholder="credential" onChange={(v) => set("auth", { bearer: v })} />
            )}
            {kind === "basic" && (
              <>
                <Combo mono value={basic[0]} choices={credChoices} placeholder="user credential" onChange={(v) => set("auth", { basic: [v, basic[1] ?? "password"] })} />
                <Combo mono value={basic[1]} choices={credChoices} placeholder="password credential" onChange={(v) => set("auth", { basic: [basic[0] ?? "user", v] })} />
              </>
            )}
          </div>
          {(kind === "header" || kind === "query") && (
            <KeyValues value={auth[kind]} onChange={(v) => set("auth", { [kind]: v })} valueChoices={() => credChoices} />
          )}
          </Field>
        {credRows.length > 0 && (
          <Field label="Credentials" help="Where each credential named above is read, on the sequencer host: an environment variable or a file (a Docker secret). Its value is never written in this file.">
            <Credentials value={creds} rows={credRows} help={credHelp} onChange={onCredentials}
              unused={(k) => !needed.includes(k)} />
          </Field>
        )}
        <Field label="Headers" help={help.headers}>
          <KeyValues value={options.headers} onChange={(v) => set("headers", v)} />
        </Field>
        <Field label="Timeout (s)" help={help.timeout}><Text mono value={options.timeout} placeholder="30" onChange={(v) => set("timeout", scalar(v))} /></Field>
        <Field label="Verify TLS" help={help.verify}>
          <Text mono value={options.verify === undefined ? "" : String(options.verify)} placeholder="true, false, or a CA bundle path"
            onChange={(v) => set("verify", scalar(v))} />
        </Field>
      </Group>
      <Group title="Steps" note="optional calls made first, each extracting variables for the next ones" help={help.steps}>
        <Steps value={options.steps} onChange={(v) => set("steps", v.length ? v : undefined)} runTest={runTest} />
      </Group>
      <Group title="List call" note="reads every requirement" help={help.list}>
        <StageTest upto="list" runTest={runTest} label="Test the first page"
          title="Run the steps, then the list call's first page: its URL, the answer, how many items, and the requirements the fields give" />
        <Field label="URL suffix" help={urlHelp("The URL of the list call")}><Text mono grow="wrap" value={list.url} placeholder="items?project=12&page={{ page }}" onChange={(v) => setIn("list", list, "url", v.trim())} /></Field>
        <Field label="Items (jq)" help={`The requirements of an answer, one item each. ${JQ_HELP}`}><Text mono grow="lines" value={list.items} placeholder=".data[]" onChange={(v) => setIn("list", list, "items", v)} /></Field>
        <Field label="Next page (jq)" help={`The URL of the next page, from an answer (none: the last page). ${JQ_HELP}`}><Text mono grow="lines" value={list.next} placeholder="optional: .links.next" onChange={(v) => setIn("list", list, "next", v)} /></Field>
        <Field label="First page" help={help.first_page}><Text mono value={options.first_page} placeholder="1" onChange={(v) => set("first_page", scalar(v))} /></Field>
        <Field label="Method">
          <select value={String(list.method || "GET").toUpperCase()} onChange={(e) => setIn("list", list, "method", e.target.value === "GET" ? undefined : e.target.value)}>
            <option>GET</option><option>POST</option>
          </select>
        </Field>
        {String(list.method || "").toUpperCase() === "POST" && (
          <Field label="Body (JSON)" help={BODY_HELP}><JsonText value={list.body} onChange={(v) => setIn("list", list, "body", v)} /></Field>
        )}
      </Group>
      <Group title="Get by id call" note="optional: fetches only the requirements needed" help={help.get}>
        <Field label="URL suffix" help={urlHelp("The URL of the get by id call, with `{{ ids }}` (comma-separated: one call) or `{{ id }}` (one call each)")}><Text mono grow="wrap" value={get.url} placeholder="items?ids={{ ids }} or items/{{ id }}" onChange={(v) => setIn("get", get, "url", v.trim())} /></Field>
        {get.url && <Field label="Items (jq)" help={`The requirements of an answer, one item each. ${JQ_HELP}`}><Text mono grow="lines" value={get.items} placeholder=".data[]" onChange={(v) => setIn("get", get, "items", v)} /></Field>}
      </Group>
      <Group title="Details calls" note="optional: calls per requirement needed in full, for fields the list does not give" help={help.details}>
        <Field label="Item variables (jq)" help={help.item_vars}>
          <KeyValues grow="lines" placeholder=".id" value={options.item_vars} onChange={(v) => set("item_vars", v)} />
        </Field>
        <Details value={options.details} runTest={runTest} onChange={(v) => set("details", v.length ? v : undefined)} />
      </Group>
      <Group title="Fields" note="each field of a requirement: where it is read (the list item, or a details call's answer) and the jq expression reading it" help={help.fields}>
        <RestFields options={options} setOptions={setOptions} runTest={runTest} />
        <Field label="Link template" help={help.url}>
          <Text mono grow="wrap" value={options.url} placeholder="https://reqs.example.com/items/{{ id }}" onChange={(v) => set("url", v.trim())} />
        </Field>
      </Group>
    </>
  );
}

/** A titled box of related fields. */
function Group({ title, note, help, children }: { title: string; note?: string; help?: string; children: ReactNode }) {
  return (
    <fieldset className="req-group">
      <legend>{title}{help && <span className="q" title={help.replace(/[`*]/g, "")}>?</span>}{note && <span className="muted small"> {note}</span>}</legend>
      {children}
    </fieldset>
  );
}

/** A JSON value, edited as text: applied when it parses. */
function JsonText({ value, onChange }: { value: any; onChange: (v: any) => void }) {
  const [text, setText] = useState(value === undefined ? "" : JSON.stringify(value, null, 1));
  const [bad, setBad] = useState(false);
  return (
    <textarea className={classes("mono", bad && "invalid")} rows={3} value={text} onChange={(e) => {
      setText(e.target.value);
      if (!e.target.value.trim()) { setBad(false); return onChange(undefined); }
      try { onChange(JSON.parse(e.target.value)); setBad(false); } catch { setBad(true); }
    }} />
  );
}

/** Where each credential is read: an environment variable (env:NAME) or a file (file:/run/secrets/name). */
function Credentials({ value, rows, help, onChange, unused }: {
  value: Data; rows: string[]; help?: Record<string, string>; onChange: (v: Data) => void; unused?: (k: string) => boolean;
}) {
  const [credName, setCredName] = useState("");
  const setCred = (k: string, kind: "env" | "file", where: string) =>
    onChange({ ...value, [k]: `${kind}:${where.trim().replace(/^(env|file):/, "")}` });
  return (
    <div className="kv">
      {rows.map((k) => {
        const v = String(value[k] ?? "");
        const kind: "env" | "file" = v.startsWith("file:") ? "file" : "env";
        const literal = v !== "" && !/^(env|file):/.test(v);
        const where = literal ? "" : v.replace(/^(env|file):/, "");
        return (
          <div key={k} className="kv-row" title={help?.[k]}>
            <span className="mono kv-key">{k}</span>
            <select value={kind} onChange={(e) => setCred(k, e.target.value as "env" | "file", e.target.value === "file" ? `/run/secrets/${k}` : k.toUpperCase())}
              title="Read from an environment variable, or from a file such as a Docker secret">
              <option value="env">env:</option><option value="file">file:</option>
            </select>
            <input className={classes("mono kv-value", (literal || (unused && !v && !unused(k))) && "invalid")} placeholder={kind === "file" ? "/run/secrets/name" : "VARIABLE"}
              value={where} onChange={(e) => setCred(k, kind, e.target.value)} />
            {literal && <span className="bad-text small">a value written here is refused: name an environment variable or a file</span>}
            {unused?.(k) && <span className="muted small">not used</span>}
            {k in value && <button className="icon" title="Remove" onClick={() => { const next = { ...value }; delete next[k]; onChange(next); }}>×</button>}
          </div>
        );
      })}
      {!unused && (
        <div className="kv-row">
          <input className="mono kv-key" placeholder="new credential (token…)" value={credName} onChange={(e) => setCredName(e.target.value.trim())} />
          {credName && <button className="small" onClick={() => { setCred(credName, "env", credName.toUpperCase()); setCredName(""); }}>add</button>}
        </div>
      )}
    </div>
  );
}

// ── variables ──────────────────────────────────────────────────────────

const VAR_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** A variable as written: a value, or {value | jq, mandatory}. A fallback is written in the value itself (Jinja2's
 *  default filter, jq's //). */
function varParts(spec: any, key: "value" | "jq"): { value: any; mandatory: boolean } {
  if (spec && typeof spec === "object" && !Array.isArray(spec)) return { value: spec[key], mandatory: !!spec.mandatory };
  return { value: spec, mandatory: false };
}

function varSpec(key: "value" | "jq", value: any, mandatory: boolean): any {
  return mandatory ? { [key]: value ?? "", mandatory: true } : value ?? "";
}

/** Rows of variables: name, value (or jq), mandatory. Mandatory ones are the context of the requirements. */
function VarRows({ value, onChange, keyName, placeholder, namePlaceholder }: {
  value?: Data | null; onChange: (v: Data) => void; keyName: "value" | "jq"; placeholder: string; namePlaceholder: string;
}) {
  const data: Data = value || {};
  const [name, setName] = useState("");
  const bad = name && (!VAR_NAME.test(name) ? "letters, digits and _" : name in data ? "already there" : null);
  const put = (k: string, v: any) => onChange({ ...data, [k]: v });
  const add = () => { if (name && !bad) { put(name, keyName === "jq" ? "" : ""); setName(""); } };
  return (
    <div className="kv req-vars-rows">
      {Object.entries(data).map(([k, spec]) => {
        const { value: v, mandatory } = varParts(spec, keyName);
        return (
          <div key={k} className="kv-row">
            <span className={classes("mono req-var-name", mandatory && "strong")}>{k}</span>
            <GrowText className="mono kv-value" placeholder={placeholder} value={v === null || v === undefined ? "" : String(v)}
              lines={keyName === "jq"} onChange={(x) => put(k, varSpec(keyName, keyName === "jq" ? x : scalar(x), mandatory))} />
            <label className="toggle small" title="It must have a value, and is the context of the requirements: shown with them and recorded in the datapack">
              <input type="checkbox" checked={mandatory} onChange={(e) => put(k, varSpec(keyName, v, e.target.checked))} /> mandatory
            </label>
            <button className="icon" title="Remove" onClick={() => { const next = { ...data }; delete next[k]; onChange(next); }}>×</button>
          </div>
        );
      })}
      <div className="kv-row">
        <input className={classes("mono kv-key", bad && "invalid")} placeholder={namePlaceholder} value={name}
          onChange={(e) => setName(e.target.value.trim())} onKeyDown={(e) => e.key === "Enter" && add()} />
        {name && !bad && <button className="small" onClick={add}>add</button>}
        {bad && <span className="bad-text small">{bad}</span>}
      </div>
    </div>
  );
}

/** The variables of a source: {{ name }} in its options, $name in jq; a value can use the datapack keys. */
function Variables({ value, onChange }: { value?: Data | null; onChange: (v: Data) => void }) {
  return (
    <>
      <VarRows value={value} onChange={onChange} keyName="value" namePlaceholder="new variable (branch, version…)"
        placeholder="value, or {{ sut.version | default('3.2') }}" />
      {value && Object.keys(value).length > 0 && (
        <div className="hint">{"{{ name }}"} in the options, <code>$name</code> in jq expressions. A value can use the datapack keys,
          with a fallback where there are none: <code>{"{{ sut.branch | default('main') }}"}</code></div>
      )}
    </>
  );
}

/** The help of the fields reading variables: which ones, and how. */
const SUBST_HELP = "`{{ name }}` is replaced by the value of a variable (URL-encoded in a URL). It is a plain substitution, " +
  "not Jinja2: no filters, no datapack keys. The variables: those of the source (Variables, above), those extracted by " +
  "an earlier step, and `page`, `offset` (list call), `ids`, `id` (get by id call, link template). A datapack key goes " +
  "through a variable of the source: `version: \"{{ sut.version | default('3.2') }}\"`, then `{{ version }}` here.";
const JQ_HELP = "A jq expression. The variables (those of the source, those extracted by an earlier step) are `$name`; " +
  "a fallback is jq's `//` (`.id // 0`).";
const urlHelp = (what: string) => `${what}, relative to the base URL (or absolute). ${SUBST_HELP}`;
const BODY_HELP = `The JSON body of the POST. A string made of one \`{{ name }}\` keeps the value's type. ${SUBST_HELP}`;

const LIST_ONLY = ["id", "path", "version"];
const SHOWN = ["id", "title", "path"];          // always shown: what the tree and the matrix need
const ROLE: Record<string, string> = { id: "required", title: "shown in the matrix", path: "builds the tree" };

/** The fields of the rest backend, one row each where it is read: the list item (options.fields) or the answer of
 *  a details call (options.details[i].fields). id, title and path are always shown; the others when given, or added. */
function RestFields({ options, setOptions, runTest }: { options: Data; setOptions: (v: Data) => void; runTest: RunTest }) {
  const fields: Data = options.fields || {};
  const calls: Data[] = Array.isArray(options.details) ? options.details : options.details ? [options.details] : [];
  const [added, setAdded] = useState<string[]>([]);
  type Row = { f: string; from: number; expr: string };     // from: -1 the list item, else the details call's index
  const rows: Row[] = [];
  for (const f of [...SHOWN, ...REST_FIELDS.map(([x]) => x).filter((x) => !SHOWN.includes(x))]) {
    const before = rows.length;
    if (fields[f] !== undefined) rows.push({ f, from: -1, expr: String(fields[f]) });
    calls.forEach((c, i) => { if (c?.fields?.[f] !== undefined) rows.push({ f, from: i, expr: String(c.fields[f]) }); });
    if (rows.length === before && (SHOWN.includes(f) || added.includes(f))) rows.push({ f, from: -1, expr: "" });
  }
  const present = new Set(rows.map((r) => r.f));
  const write = (f: string, from: number, expr: string | undefined, next: Data = clone(options)) => {
    if (from < 0) next.fields = put(next.fields, f, expr);
    else {
      const list = (Array.isArray(next.details) ? next.details : [next.details]).map((c: Data) => ({ ...c }));
      list[from] = { ...list[from], fields: { ...(list[from].fields || {}) } };
      if (expr === undefined || expr === "") delete list[from].fields[f]; else list[from].fields[f] = expr;
      next.details = list;
    }
    if (next.fields && !Object.keys(next.fields).length) delete next.fields;
    return next;
  };
  const move = (r: Row, to: number) => setOptions(write(r.f, to, r.expr || (to < 0 ? `.${r.f}` : `.data.${r.f}`), write(r.f, r.from, undefined)));
  const what = Object.fromEntries(REST_FIELDS);
  const addable = REST_FIELDS.filter(([f]) => !present.has(f));
  // the field test: a requirement picked among those of the list, each field evaluated on it
  const [picking, setPicking] = useState<any>(null);        // the ids test: {ok, ids, items, error, calls}
  const [busy, setBusy] = useState("");
  const [picked, setPicked] = useState("");
  const [tested, setTested] = useState<any>(null);          // the item test: {ok, fields, item_vars, error, calls}
  const [debugging, setDebugging] = useState(false);
  const pick = async () => { setBusy("ids"); setPicking(null); setTested(null); setPicked(""); try { setPicking(await runTest("ids")); } finally { setBusy(""); } };
  const test = async (id: string) => {
    setPicked(id);
    if (!id) return;
    setBusy("item");
    setTested(null);
    try { setTested(await runTest(`item:${id}`)); } finally { setBusy(""); }
  };
  const results: Record<string, any> = {};
  for (const r of tested?.fields || []) results[`${r.field}:${r.from}`] = r;
  const resultOf = (r: Row) => results[`${r.f}:${r.from < 0 ? "list" : `details:${r.from + 1}`}`];
  const extra = (tested?.fields || []).filter((r: any) => r.default && !rows.some((x) => x.f === r.field && x.from < 0));
  const shown = tested?.ok;
  return (
    <>
      <div className="req-fields-test">
        <button className="small" disabled={!!busy} onClick={pick}
          title="Run the steps and read the whole list, to pick a requirement and see what each field gives for it">
          {busy === "ids" ? "Reading the list…" : picking?.ok ? "Read the list again" : "Test on a requirement…"}</button>
        {picking?.ok && (
          <Combo mono value={picked} placeholder={`pick one of ${picking.ids.length} requirement(s)`}
            choices={picking.ids.map((r: any) => ({ value: r.id, description: r.title }))}
            onChange={setPicked} onPick={test} />
        )}
        {picking?.ok && picked && <button className="small" disabled={!!busy} onClick={() => test(picked)}>{busy === "item" ? "Testing…" : "Test"}</button>}
        {picking?.ok && picked && <button className="small" onClick={() => setDebugging(!debugging)}
          title={`Evaluate any jq expression on ${DEBUG_ON.item}, with the variables known there`}>Debug…</button>}
        {picking && !picking.ok && <span className="error-text small">{picking.error}</span>}
        {tested && !tested.ok && <span className="error-text small">{tested.error}</span>}
        {(picking || tested) && <button className="link small" onClick={() => { setPicking(null); setTested(null); setPicked(""); setDebugging(false); }}>hide</button>}
      </div>
      {debugging && picking?.ok && picked && <Debug key={picked} upto={`item:${picked}`} runTest={runTest} onClose={() => setDebugging(false)}
        ons={[{ value: "list", label: "the list item" }, ...calls.map((_, i) => ({ value: `details:${i + 1}`, label: `details call ${i + 1}'s answer` }))]} />}
      <table className={classes("grid compact req-fields req-fields-from", shown && "tested")}>
        <thead><tr><th>Field</th><th>Read from</th><th>jq{shown && <span className="muted"> → value for <code>{picked}</code></span>}</th><th /></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={`${r.f}:${r.from}`} title={what[r.f]}>
            <td className="mono">{r.f}{ROLE[r.f] && <div className="muted small req-field-role">{ROLE[r.f]}</div>}</td>
            <td>
              <select value={r.from} disabled={LIST_ONLY.includes(r.f) || !calls.length} onChange={(e) => move(r, Number(e.target.value))}
                title={LIST_ONLY.includes(r.f) ? "From the list only: the tree, the matrix and the versions are made without details calls"
                  : !calls.length ? "Add a details call to read it from there" : "Where the field is read"}>
                <option value={-1}>list item</option>
                {calls.map((c, i) => <option key={i} value={i} title={c?.url || undefined}>details call {i + 1}</option>)}
              </select>
            </td>
            <td><GrowText className="mono" lines value={r.expr} placeholder={r.from < 0 ? `.${r.f}` : `.data.${r.f}`}
              onChange={(v) => setOptions(write(r.f, r.from, v))} />
              {shown && <div className="req-field-value">→ <FieldValue r={resultOf(r)} /></div>}</td>
            <td>{r.f !== "id" && (
              <button className="icon" title={SHOWN.includes(r.f) ? "Clear it (read as ." + r.f + " when the item has it)" : "Remove this field"}
                onClick={() => { setAdded(added.filter((x) => x !== r.f)); setOptions(write(r.f, r.from, undefined)); }}>×</button>
            )}</td>
          </tr>
        ))}</tbody>
      </table>
      {shown && (
        <div className="req-fields-tested small">
          {extra.length > 0 && <div>Also read by default (the item's key of the same name): {extra.map((r: any) => (
            <VarValue key={r.field} name={r.field} value={r.value} />))}</div>}
          {Object.keys(tested.vars || {}).length > 0 && <div>Variables (the source's, and those the steps extracted, their calls below): {Object.entries<any>(tested.vars).map(([k, v]) => (
            <VarValue key={k} name={k} value={v} />))}</div>}
          {Object.keys(tested.item_vars || {}).length > 0 && <div>Item variables: {Object.entries<any>(tested.item_vars).map(([k, v]) => (
            <VarValue key={k} name={k} value={v} />))}</div>}
          {tested.first_item && <details className="req-call-part"><summary>The list item of <code>{picked}</code></summary>
            <pre className="mono small">{tested.first_item}</pre></details>}
        </div>
      )}
      {(tested || picking) && <Calls result={tested || picking} />}
      <div className="req-fields-add">
        {addable.length > 0 && (
          <select value="" onChange={(e) => e.target.value && setAdded([...added, e.target.value])} title="Another field of the requirements">
            <option value="">Add a field…</option>
            {addable.map(([f, w]) => <option key={f} value={f}>{f}: {w}</option>)}
          </select>
        )}
        <span className="hint">A field not listed is read from the list item's key of the same name when it has one (<code>.status</code>). Fields of a details call
          are read only for the requirements needed in full (generation, a requirement opened), so keep title and status in the list.</span>
      </div>
    </>
  );
}

/** The rest backend's details calls: per requirement, each giving fields from its answer. */
function Details({ value, onChange, runTest }: { value?: any[] | null; onChange: (v: any[]) => void; runTest: RunTest }) {
  const calls: Data[] = Array.isArray(value) ? value : value && typeof value === "object" ? [value] : [];
  const setCall = (i: number, call: Data) => onChange(calls.map((s, j) => (j === i ? call : s)));
  const setIn = (i: number, k: string, v: any) => setCall(i, put(calls[i], k, v));
  return (
    <div className="req-steps">
      {calls.map((call, i) => {
        const fields: Data = call.fields || {};
        return (
          <div key={i} className="req-step">
            <div className="req-step-head">
              <strong className="small">Details call {i + 1}</strong>
              <span className="grow" />
              <button className="icon" title="Remove this call" onClick={() => onChange(calls.filter((_, j) => j !== i))}>×</button>
            </div>
            <StageTest upto={`details:${i + 1}`} runTest={runTest} label="Test on the first requirement"
              title="Run the steps and the list call's first page, then this call for its first requirement: its URL, the answer, and the fields it gives" />
            <Field label="URL suffix" help={urlHelp("The URL of this call, for one requirement: `{{ id }}` and the item variables")}>
              <Text mono grow="wrap" value={call.url} placeholder="items/{{ item_id }}" onChange={(v) => setIn(i, "url", v.trim())} /></Field>
            <Field label="Fields" help="The fields read from this call's answer: pick this call as their origin under Fields, below.">
              <span className="small">{Object.keys(fields).length ? Object.keys(fields).map((f) => <code key={f} className="req-field-chip">{f}</code>)
                : <span className="muted">none yet: pick this call for a field under Fields, below (else it is not made)</span>}</span>
            </Field>
            <Field label="Cache"><Text mono value={call.cache} placeholder="none; or 10m, 1h, 1d (read again when its version changes)" onChange={(v) => setIn(i, "cache", scalar(v.trim()))} /></Field>
            <Field label="Method">
              <select value={String(call.method || "GET").toUpperCase()} onChange={(e) => setIn(i, "method", e.target.value === "GET" ? undefined : e.target.value)}>
                <option>GET</option><option>POST</option>
              </select>
            </Field>
            {String(call.method || "").toUpperCase() === "POST" && (
              <Field label="Body (JSON)" help={BODY_HELP}><JsonText value={call.body} onChange={(v) => setIn(i, "body", v)} /></Field>
            )}
          </div>
        );
      })}
      <button className="small" onClick={() => onChange([...calls, { url: "", fields: {} }])}>Add details call</button>
    </div>
  );
}

/** The rest backend's steps: calls made before the others, each extracting variables with jq. */
function Steps({ value, onChange, runTest }: { value?: any[] | null; onChange: (v: any[]) => void; runTest: RunTest }) {
  const steps: Data[] = Array.isArray(value) ? value : [];
  const setStep = (i: number, step: Data) => onChange(steps.map((s, j) => (j === i ? step : s)));
  const setIn = (i: number, k: string, v: any) => setStep(i, put(steps[i], k, v));
  return (
    <div className="req-steps">
      {steps.map((step, i) => (
        <div key={i} className="req-step">
          <div className="req-step-head">
            <strong className="small">Step {i + 1}</strong>
            <span className="grow" />
            <button className="icon" title="Remove this step" onClick={() => onChange(steps.filter((_, j) => j !== i))}>×</button>
          </div>
          <StageTest upto={`step:${i + 1}`} runTest={runTest} label={i ? `Test steps 1 to ${i + 1}` : "Test this step"}
            title="Call the server for this step (and the earlier ones, whose variables it reads), as in this form: each call's URL, headers, status and answer, and the variables extracted. The cache is not used." />
          <Field label="URL suffix" help={urlHelp("The URL of this step")}><Text mono grow="wrap" value={step.url} placeholder="systems?branch={{ branch }}" onChange={(v) => setIn(i, "url", v.trim())} /></Field>
          <Field label="Variables (jq)" help={`Each variable this step extracts from its answer, for the next steps and calls: one value at most (none: no value, an error when mandatory). ${JQ_HELP}`}>
            <VarRows value={step.extract} onChange={(v) => setIn(i, "extract", v)} keyName="jq"
              namePlaceholder="new variable (subsystem_id…)" placeholder=".items[] | select(.name == $subsystem) | .id   (fallback: // 0)" />
          </Field>
          <Field label="Cache"><Text mono value={step.cache} placeholder="none: once per generation; or 10m, 1h, 1d" onChange={(v) => setIn(i, "cache", scalar(v.trim()))} /></Field>
          <Field label="Method">
            <select value={String(step.method || "GET").toUpperCase()} onChange={(e) => setIn(i, "method", e.target.value === "GET" ? undefined : e.target.value)}>
              <option>GET</option><option>POST</option>
            </select>
          </Field>
          {String(step.method || "").toUpperCase() === "POST" && (
            <Field label="Body (JSON)" help={BODY_HELP}><JsonText value={step.body} onChange={(v) => setIn(i, "body", v)} /></Field>
          )}
        </div>
      ))}
      <button className="small" onClick={() => onChange([...steps, { url: "", extract: {} }])}>Add step</button>
    </div>
  );
}

// ── tests of a source: what each call to the server does ───────────────

type RunTest = (upto?: string, debug?: { expr: string; on?: string }) => Promise<any>;

/** Debug: any jq expression on what a stage's jq reads (a step's answer, the list's first page, a details call's
 * answer, a list item), with the variables known there: every value it gives, or its error. */
function Debug({ upto, runTest, ons, onClose }: { upto: string; runTest: RunTest; ons?: { value: string; label: string }[]; onClose: () => void }) {
  const [expr, setExpr] = useState(".");
  const [on, setOn] = useState(ons?.[0]?.value || "list");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const run = async () => {
    if (!expr.trim()) return;
    setBusy(true);
    setResult(null);
    try { setResult(await runTest(upto, { expr, on })); } finally { setBusy(false); }
  };
  const e = result?.expr;
  const known = Object.keys({ ...(result?.vars || {}), ...(result?.item_vars || {}) });
  return (
    <div className="req-debug">
      <div className="req-debug-row">
        {ons && ons.length > 1 && (
          <select value={on} onChange={(ev) => setOn(ev.target.value)} title="What the expression reads">
            {ons.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        )}
        <GrowText className="mono grow" lines value={expr} placeholder=". | keys   ($variable, [.data[] | .id]…)"
          onChange={(v) => setExpr(v)} onKeyDown={(ev) => { if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); run(); } }} />
        <button className="small" disabled={busy || !expr.trim()} onClick={run}
          title="Evaluate this jq expression, with the variables known at this stage (Ctrl+Enter)">{busy ? "Running…" : "Run"}</button>
        <button className="link small" onClick={onClose}>close</button>
      </div>
      {result && !result.ok && <div className="error-text small">{result.error}</div>}
      {e && (
        <div className={classes("req-test small", e.error ? "bad" : "ok")}>
          <div className="muted">On {e.on}{known.length > 0 && <>; variables: {known.map((k) => <code key={k}>${k} </code>)}</>}</div>
          {e.error ? <div className="error-text">{e.error}</div>
            : e.count === 0 ? <div className="muted">No value (empty).</div>
            : <>
              {e.values.map((v: any, i: number) => (
                <pre key={i} className="mono small req-var-box">{typeof v === "string" ? JSON.stringify(v) : JSON.stringify(v, null, 1)}</pre>
              ))}
              {e.count > e.values.length && <div className="muted">and {e.count - e.values.length} more value(s)</div>}
            </>}
        </div>
      )}
      {result?.calls?.length > 0 && <details className="req-call-part"><summary className="small muted">{result.calls.length} call(s) to the server</summary>
        <Calls result={result} /></details>}
    </div>
  );
}

const DEBUG_ON: Record<string, string> = {
  step: "the answer of this step (the variables of the steps before it)", list: "the answer of the list call's first page",
  details: "the answer of this call for the first requirement", item: "the list item of this requirement, or a details call's answer",
};

/** A Test button for a stage of a source (a step, the list call), and what its calls did. */
function StageTest({ upto, runTest, label, title }: { upto: string; runTest: RunTest; label: string; title: string }) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [debugging, setDebugging] = useState(false);
  const test = async () => {
    setTesting(true);
    setResult(null);
    try { setResult(await runTest(upto)); } finally { setTesting(false); }
  };
  return (
    <div className="req-stage-test">
      <button className="small" disabled={testing} onClick={test} title={title}>{testing ? "Testing…" : `${label}…`}</button>{" "}
      <button className="small" onClick={() => setDebugging(!debugging)}
        title={`Evaluate any jq expression on ${DEBUG_ON[upto.split(":")[0]]}, with the variables known there`}>Debug…</button>
      {debugging && <Debug upto={upto} runTest={runTest} onClose={() => setDebugging(false)} />}
      {result && (
        <div className={classes("req-test", result.ok ? "ok" : "bad")}>
          {result.ok ? (
            <>
              <div>Done in {result.seconds} s{upto === "list" && <>: <strong>{result.items}</strong> item(s) in the first page</>}.</div>
              {result.vars && Object.keys(result.vars).length > 0 && (
                <div className="req-vars small">Variables: {Object.entries<any>(result.vars).map(([k, v]) => (
                  <VarValue key={k} name={k} value={v} />
                ))}</div>
              )}
              {result.sample?.length > 0 && (
                <table className="grid compact">
                  <thead><tr><th>Id</th><th>Path</th><th>Title</th><th>Status</th></tr></thead>
                  <tbody>{result.sample.map((r: any, n: number) => (
                    <tr key={n}><td>{r.id ? <code>{r.id}</code> : <span className="bad-text small">no id: left out</span>}</td><td className="small">{r.path}</td><td>{r.title}</td><td>{r.status}</td></tr>
                  ))}</tbody>
                </table>
              )}
              {result.item_vars && Object.keys(result.item_vars).length > 0 && (
                <div className="req-vars small">Item variables: {Object.entries<any>(result.item_vars).map(([k, v]) => (
                  <VarValue key={k} name={k} value={v} />
                ))}</div>
              )}
              {result.requirement && (
                <table className="grid compact">
                  <tbody>{Object.entries<any>(result.requirement).filter(([, v]) => v !== "" && !(Array.isArray(v) && !v.length)).map(([k, v]) => (
                    <tr key={k}><td className="mono small">{k}</td><td className="small" style={{ whiteSpace: "pre-wrap" }}>{Array.isArray(v) ? v.join(", ") : String(v)}</td></tr>
                  ))}</tbody>
                </table>
              )}
              {result.first_item && (
                <details className="req-call-part"><summary className="small">The first item (what the field expressions read)</summary>
                  <pre className="mono small">{result.first_item}</pre></details>
              )}
            </>
          ) : <div className="error-text">{result.error}</div>}
          <Calls result={result} openLast={upto.startsWith("step:") || upto.startsWith("details:")} />
          <button className="link small" onClick={() => setResult(null)}>hide</button>
        </div>
      )}
    </div>
  );
}

/** A field's value in the field test, or its error. */
function FieldValue({ r }: { r?: any }) {
  if (!r) return <span className="muted small">not tested (save or test again)</span>;
  if (r.error) return <span className="error-text small">{r.error}</span>;
  const v = r.value;
  if (v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length)) return <span className="muted small">empty</span>;
  return <span className="small req-field-shown">{Array.isArray(v) ? v.join(", ") : String(v)}</span>;
}

/** A variable and its value in a test's result: a long value (a whole structure a step extracted) cut on one line,
 *  a click showing it whole in a box of limited height, so that what follows stays in view. */
function VarValue({ name, value }: { name: string; value: any }) {
  const [open, setOpen] = useState(false);
  const text = shownValue(value);
  if (text.length <= 100) return <span className="mono">{name}={text}</span>;
  const pretty = typeof value === "object" && value !== null ? JSON.stringify(value, null, 1) : text;
  return (
    <span className={classes("mono req-var-long", open && "open")}>
      <button className="link small" onClick={() => setOpen(!open)} title={open ? "Fold it" : `Show it whole (${text.length} characters)`}>
        {name}={open ? "" : text.slice(0, 100) + "…"}{open ? "▾" : " ▸"}</button>
      {open && <pre className="mono small req-var-box">{pretty}</pre>}
    </span>
  );
}

const shownValue = (v: any) => (typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? "") || "∅");

/** The calls a test made: each with its method, full URL (credentials masked) and status, opened on the details. */
function Calls({ result, openLast }: { result: any; openLast?: boolean }) {
  const calls: any[] = result?.calls || [];
  if (!calls.length) return null;
  return (
    <div className="req-calls">
      <div className="small muted">{calls.length} call(s) to the server{result.calls_more ? `, and ${result.calls_more} more not shown` : ""}:</div>
      {calls.map((c, i) => <Call key={i} call={c} open={(openLast || !result.ok) && i === calls.length - 1} />)}
    </div>
  );
}

function Call({ call, open }: { call: any; open: boolean }) {
  const bad = !!call.error || (call.status ?? 0) >= 400;
  const copy = () => navigator.clipboard?.writeText(call.url).catch(() => undefined);
  return (
    <details className={classes("req-call", bad && "bad")} open={open}>
      <summary>
        <span className="small req-call-name">{call.call}</span>{" "}
        <span className={classes("req-call-status mono small", bad ? "bad-text" : "ok")}>
          {call.status ? `${call.status} ${call.reason || ""}` : "no answer"}</span>{" "}
        <span className="mono small">{call.method}</span>{" "}
        <span className="mono small req-call-url">{call.url}</span>
        {call.seconds !== undefined && <span className="muted small"> {call.seconds} s</span>}
      </summary>
      <div className="req-call-body">
        <div className="req-call-url-row">
          <code className="small">{call.url}</code>
          <button className="link small" onClick={copy} title="Copy the URL (credentials masked: ***)">copy</button>
        </div>
        {call.error && <div className="error-text small">{call.error}</div>}
        {call.extracted && Object.keys(call.extracted).length > 0 && (
          <div className="req-vars small">Extracted: {Object.entries<any>(call.extracted).map(([k, v]) => <VarValue key={k} name={k} value={v} />)}</div>
        )}
        {call.items !== undefined && <div className="small">{call.items} item(s){call.next ? <>; next page: <code>{call.next}</code></> : ""}</div>}
        <details className="req-call-part"><summary className="small">Request headers</summary>
          <pre className="mono small">{Object.entries<any>(call.headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n")}</pre></details>
        {call.body !== undefined && (
          <details className="req-call-part"><summary className="small">Request body</summary>
            <pre className="mono small">{JSON.stringify(call.body, null, 1)}</pre></details>
        )}
        {call.answer !== undefined && (
          <details className="req-call-part" open={bad || !!call.extracted}>
            <summary className="small">Answer{call.content_type ? ` (${call.content_type}, ${call.size} bytes${call.cut ? ", the beginning" : ""})` : ""}</summary>
            <pre className="mono small">{call.answer || "(empty)"}</pre></details>
        )}
      </div>
    </details>
  );
}
