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
import { useMemo, useState } from "react";
import { Combo, Field, helpText, IdText, KeyValues, Section, Tags, Text, type Choice } from "../fields";
import { send } from "../api";
import { classes, joinPath } from "../util";
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
      {sourcesFile && (
        <Section title="Sources" help={help(["sources"])}
          actions={!hasSources && (
            <button className="small" title="Keep the requirements of this file as the source 'reqs', and add sources read by other backends"
              onClick={() => setTop("sources", { reqs: { backend: "yaml" } })}>Use several sources…</button>
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
          backends={backends} backendChoices={backendChoices} help={help}
          onChange={(spec) => setSource(n, spec)}
          onRename={(to) => {
            const next = clone(data);
            next.sources = renamed(sources, n, to);
            if (next.default === n) next.default = to;
            setAll(next);
          }}
          onDefault={() => setTop("default", n === names[0] ? undefined : n)}
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

function SourceCard({ name, spec, doc, isDefault, taken, backends, backendChoices, help, onChange, onRename, onDefault, onRemove }: {
  name: string; spec: Data; doc: Doc; isDefault: boolean; taken: string[]; backends: any[]; backendChoices: Choice[];
  help: (p: (string | number)[]) => string | undefined;
  onChange: (spec: Data) => void; onRename: (to: string) => void; onDefault: () => void; onRemove: () => void;
}) {
  const { sid, files, open } = useSession();
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const test = async () => {
    setTesting(true);
    try {
      setResult(await send("POST", `edit/sessions/${sid}/requirements/test`, { path: doc.path, source: name }));
    } catch (e: any) {
      setResult({ ok: false, error: String(e?.message || e) });
    } finally {
      setTesting(false);
    }
  };
  const backend = backends.find((b) => b.name === String(spec.backend || "").toLowerCase());
  const options: Data = spec.options || {};
  const credentials: Data = spec.credentials || {};
  const set = (k: string, v: any) => onChange({ ...spec, [k]: v });
  const knownOptions: Choice[] = Object.entries<string>(backend?.options || {}).map(([value, description]) => ({ value, description }));
  const knownCredentials: string[] = Object.keys(backend?.credentials || {});
  // the file a yaml source reads, in the session
  const yamlFile = backend?.name === "yaml" ? joinPath(doc.path, String(options.file || SOURCES_FILE)) : null;
  const yamlFound = yamlFile && files.some((f) => f.path === yamlFile);
  const credRows = [...new Set([...Object.keys(credentials), ...knownCredentials])];
  const [credName, setCredName] = useState("");
  const setCred = (k: string, variable: string) => {
    const next = { ...credentials };
    const v = variable.trim().replace(/^env:/, "");
    if (v) next[k] = `env:${v}`;
    else delete next[k];
    set("credentials", next);
  };
  return (
    <div className={classes("req-source", isDefault && "default")}>
      <div className="req-source-head">
        <IdText value={name} taken={taken} onChange={onRename} />
        <label className="toggle" title="The source of the references without a prefix">
          <input type="radio" checked={isDefault} onChange={onDefault} /> default
        </label>
        <span className="grow" />
        <button className="small" disabled={testing} onClick={test}
          title="Read this source as datapack generation does, from the file as saved: its credentials are read from the environment of the sequencer host">
          {testing ? "Testing…" : "Test connection"}</button>
        <button className="icon" title="Remove this source" onClick={onRemove}>×</button>
      </div>
      {result && (
        <div className={classes("req-test", result.ok ? "ok" : "bad")}>
          {result.ok ? (
            <>
              <div><strong>{result.count}</strong> requirement(s), {result.nodes} node(s), read in {result.seconds} s{result.count > result.sample.length ? `; the first ${result.sample.length}:` : ":"}</div>
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
          <button className="link small" onClick={() => setResult(null)}>hide</button>
        </div>
      )}
      <Field label="Backend" help={help(["sources", name, "backend"])}
        error={spec.backend && !backend ? `no backend '${spec.backend}' here: a plugin registers it` : null}>
        <Combo mono value={spec.backend} onChange={(v) => set("backend", v)} choices={backendChoices} />
      </Field>
      <Field label="Label" help={help(["sources", name, "label"])}><Text value={spec.label} onChange={(v) => set("label", v)} placeholder={name} /></Field>
      <Field label="Options" help={help(["sources", name, "options"])} wide
        hint={yamlFile && (yamlFound
          ? <button className="link small" onClick={() => open(yamlFile)}>open {yamlFile}</button>
          : <span className="bad-text small">no file {yamlFile} in the session</span>)}>
        <KeyValues value={options} onChange={(v) => set("options", v)} known={knownOptions} />
      </Field>
      {(credRows.length > 0 || backend?.name !== "yaml") && (
        <Field label="Credentials" help={help(["sources", name, "credentials"])} wide>
          <div className="kv">
            {credRows.map((k) => {
              const value = String(credentials[k] ?? "");
              const literal = value && !value.startsWith("env:");
              return (
                <div key={k} className="kv-row" title={backend?.credentials?.[k]}>
                  <span className="mono kv-key">{k}</span>
                  <span className="muted">env:</span>
                  <input className={classes("mono kv-value", literal && "invalid")} placeholder="VARIABLE"
                    value={literal ? "" : value.replace(/^env:/, "")} onChange={(e) => setCred(k, e.target.value)} />
                  {literal && <span className="bad-text small">a value written here is refused: name an environment variable</span>}
                  <button className="icon" title="Remove" onClick={() => { const next = { ...credentials }; delete next[k]; set("credentials", next); }}>×</button>
                </div>
              );
            })}
            <div className="kv-row">
              <input className="mono kv-key" placeholder="new credential (token…)" value={credName} onChange={(e) => setCredName(e.target.value.trim())} />
              {credName && <button className="small" onClick={() => { setCred(credName, credName.toUpperCase()); setCredName(""); }}>add</button>}
            </div>
          </div>
        </Field>
      )}
    </div>
  );
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
              <thead><tr><th /><th>Id</th><th>Path</th><th>Title</th><th>Status</th><th>Tags</th><th /></tr></thead>
              <tbody>
                {shown.map((id) => (
                  <RequirementRow key={id} id={id} body={reqs[id] || {}} taken={ids} open={openId === id} pathChoices={pathChoices} help={help}
                    onToggle={() => setOpenId(openId === id ? null : id)}
                    onChange={(b) => setReq(id, b)}
                    onRename={(to) => { setTop("requirements", renamed(reqs, id, to)); if (openId === id) setOpenId(to); }}
                    onRemove={() => setReq(id, undefined)} />
                ))}
                {shown.length === 0 && <tr><td colSpan={7} className="muted">No requirement matches the filter.</td></tr>}
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
        <td><Combo mono value={body.path} onChange={(v) => set("path", v.replace(/^\/+|\/+$/g, ""))} choices={pathChoices} placeholder="SYS/BRAKE" /></td>
        <td><Text value={body.title} onChange={(v) => set("title", v)} /></td>
        <td><Combo value={body.status} onChange={(v) => set("status", v)} choices={STATUSES} /></td>
        <td><Tags value={(body.tags || []).map(String)} onChange={(v) => set("tags", v)} /></td>
        <td><button className="icon" title="Remove this requirement" onClick={onRemove}>×</button></td>
      </tr>
      {open && (
        <tr className="req-edit-detail">
          <td />
          <td colSpan={6}>
            <div className="form">
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
