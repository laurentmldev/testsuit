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

// Issues form (sequencer README, section 10.6): issues.yml, where the issues of the tests repository go. Its sources
// (a backend, its options and credentials, labels), the default one, the routes that pick the source of a new issue,
// and how an issue names the requirements it is about.
import { useState } from "react";
import { Combo, Field, IdText, KeyValues, Section, Tags, Text, Toggle, type Choice } from "../fields";
import { send } from "../api";
import { classes } from "../util";
import { IssueTypes } from "../pages/Issues";
import { ClassSourceForm, ClassVars, Credentials } from "./RequirementsForm";
import { clone, useSession, type Doc } from "./state";

type Data = Record<string, any>;
const NAME = /^[A-Za-z_][A-Za-z0-9_-]*$/;
const ROUTE_KEYS: (Choice & { example: string })[] = [
  { value: "testdef", description: "the name of the test definition that failed", example: "speed_*" },
  { value: "step", description: "the step that failed, or holds the check: <stage>:<step> (a failed verdict has no step)", example: "firmware:*" },
  { value: "criteria", description: "the criteria file of an unmet check (only checks have one)", example: "criteria/firmware/*" },
  { value: "check", description: "the name of an unmet check (only checks have one)", example: "speed_*" },
];
const ROUTES_HELP = `Where a new issue goes. When someone reports a failure (Issue… on the Run page, or tsq issue --create), the routes are read top to bottom: the first one whose patterns all match the failure picks the source; when none matches, the default source.

A pattern is a glob: * any text, ? one character, [abc] one of them; it must match the whole value, case counts. A pattern left empty (shown *) matches anything.

- testdef: the test definition's name.
- step: <stage>:<step>, the failed step, or the step that holds an unmet check (exploit:criteria). A failed verdict has no step: a route with a step pattern never takes it.
- criteria, check: only for an unmet check: its criteria file, its name.

Examples: step firmware:* sends the failures of the firmware stage to the firmware project; criteria criteria/bench/* and check calib_* send those checks to the bench project. Put the most precise routes first. The person reporting can still pick another source before creating the issue.`;
const REQS_HELP = `How an issue says which requirements it is about, besides the requirements its failure verifies (those come from the links of the check, step or test definition it is bound to, with no setting).

- Labels: an issue labelled with the pattern, {id} being a requirement's id (req:{id} → req:REQ-12, req:jama:SYS-4) names it. With "give such labels", tsq adds one label per requirement of the failure to the issues it creates (not on Redmine, which has no labels).
- Text: the id of a known requirement, as a whole word in the issue's title or text, names it (REQ-12 names REQ-12, not REQ-123).
- Jira links: a Jira issue linked to the Jira issue of a requirement (its id being the Jira key) names it.

Known requirements are those the test definitions of this repository link (reqs). The Requirements subtab of Datapacks shows, for each requirement, its open issues and why each is about it. A source can have its own rules (in its card), e.g. labels off for a tool where labels are used otherwise.`;
const FILTER_HELP = `Which of the issues its tool returns this source keeps (listed, searched, matched to requirements); every condition given must hold. An issue named by its reference is read whatever the filter.

- state: open, closed or all: the issues the source ever lists.
- labels: every one of them; any labels: at least one; not labels: none of them (case independent).
- text, not text: a regular expression found (or not) in the title or the text, case independent: speed|counter.
- title: a regular expression found in the title: ^\\[tsq\\].
- projects: a group source: globs on the projects' paths (fw/*).
- authors, assignees: their user names.
- created / updated after, before: a date (2026-01-31), a date and time, or a time ago (90d, 12w, 6m, 1y).

State and labels are asked of the tool (fewer issues read); the rest is applied to what it returns (at most 5,000 read).`;
const FILTER_KEYS: { key: string; label: string; kind: "state" | "list" | "rx" | "date"; placeholder?: string }[] = [
  { key: "state", label: "State", kind: "state" },
  { key: "labels", label: "Every label", kind: "list", placeholder: "bench" },
  { key: "any_labels", label: "Any label", kind: "list", placeholder: "bug" },
  { key: "not_labels", label: "No label", kind: "list", placeholder: "wontfix" },
  { key: "text", label: "Text", kind: "rx", placeholder: "speed|counter" },
  { key: "not_text", label: "Not text", kind: "rx", placeholder: "duplicate of" },
  { key: "title", label: "Title", kind: "rx", placeholder: "^\\[tsq\\]" },
  { key: "projects", label: "Projects", kind: "list", placeholder: "fw/*" },
  { key: "authors", label: "Authors", kind: "list", placeholder: "alice" },
  { key: "assignees", label: "Assignees", kind: "list", placeholder: "carol" },
  { key: "created_after", label: "Created after", kind: "date", placeholder: "2026-01-01 or 90d" },
  { key: "created_before", label: "Created before", kind: "date", placeholder: "2026-12-31" },
  { key: "updated_after", label: "Updated after", kind: "date", placeholder: "90d" },
  { key: "updated_before", label: "Updated before", kind: "date", placeholder: "1y" },
];
const DATE = /^(\d+\s*[dwmyDWMY]|\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?([.]\d+)?(Z|[+-]\d{2}:?\d{2})?)?)$/;

const REQ_DEFAULTS: Data = { labels: "req:{id}", text: true, jira_links: true, add: true };

function pruned(obj: Data): Data {
  const out: Data = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)
      || (typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length)) continue;
    out[k] = v;
  }
  return out;
}

export function IssuesForm({ doc, api }: { doc: Doc; api: { setData: (d: any) => void } }) {
  const { ctx } = useSession();
  const data: Data = doc.data || {};
  const backends: any[] = ctx.issue_backends || [];
  const sources: Data = data.sources || {};
  const names = Object.keys(sources);
  const defaultName = data.default || names[0];
  const setTop = (k: string, v: any) => {
    const next = clone(data);
    if (v === undefined || v === "" || (typeof v === "object" && v !== null && !Array.isArray(v) && !Object.keys(v).length)) delete next[k];
    else next[k] = v;
    api.setData(next);
  };
  const setSource = (n: string, spec: Data | undefined) => {
    const next = clone(sources);
    if (spec === undefined) delete next[n];
    else next[n] = pruned(spec);
    setTop("sources", next);
  };
  const [name, setName] = useState("");
  const [added, setAdded] = useState<string | null>(null);
  const bad = name && !NAME.test(name) ? "letters, digits, _ and - (the prefix of its references: name#42)" : name in sources ? "already a source" : null;
  const create = () => {
    if (!name || bad) return;
    setSource(name, { backend: backends[0]?.name || "gitlab" });
    setAdded(name);
    setName("");
  };
  return (
    <div className="form pad issues-form">
      <Section title="Sources" help="Each source is one project of an issue tracker (or a GitLab group, a GitHub organization): where issues are read, and created. References name it: name#42 (#42 alone: the default source).">
        <div className="req-new">
          <strong>New source</strong>
          <input className="mono" placeholder="its name: the prefix of its references (bench, firmware, jira…)" value={name}
            onChange={(e) => setName(e.target.value.trim())} onKeyDown={(e) => e.key === "Enter" && create()} />
          <button className="primary" disabled={!name || !!bad} onClick={create}>+ Create source</button>
          {bad && <span className="bad-text small">{bad}</span>}
        </div>
        {names.length === 0 && <p className="muted">No source yet: failures cannot be reported nor bound to issues.</p>}
        {names.map((n) => (
          <IssueSource key={n} name={n} spec={sources[n] || {}} data={data} backends={backends} taken={names}
            isDefault={n === defaultName} startOpen={added === n || names.length === 1}
            onChange={(spec) => setSource(n, spec)}
            onRename={(to) => {
              const next = clone(data);
              next.sources = Object.fromEntries(Object.entries(sources).map(([k, v]) => [k === n ? to : k, v]));
              if (next.default === n) next.default = to;
              next.routes = (next.routes || []).map((r: Data) => (r?.source === n ? { ...r, source: to } : r));
              api.setData(next);
            }}
            onDefault={() => setTop("default", n === names[0] ? undefined : n)}
            onRemove={() => {
              const next = clone(data);
              delete next.sources[n];
              if (next.default === n) delete next.default;
              next.routes = (next.routes || []).filter((r: Data) => r?.source !== n);
              if (!next.routes.length) delete next.routes;
              api.setData(next);
            }} />
        ))}
        <p className="muted small">Backends available here: {backends.map((b) => b.name).join(", ") || "none"} (a plugin adds one
          with <code>register_issue_backend</code>). Credentials are read on the sequencer host, from an environment variable or a file.</p>
      </Section>
      <Section title="Classes" collapsed={!Object.keys(data.classes || {}).length} help="What several sources share (a server, how they sign in, a filter), written once with variables that each source of the class gives: a GitLab server with a project per source, a Jira site with a component per source.">
        <Classes data={data} backends={backends} setAll={api.setData} />
      </Section>
      <Section title="Routes" help={ROUTES_HELP}>
        <Routes value={data.routes || []} names={names} defaultName={defaultName} onChange={(v) => setTop("routes", v.length ? v : undefined)} />
      </Section>
      <Section title="Requirements" help={REQS_HELP}>
        <ReqRules value={data.requirements} onChange={(v) => setTop("requirements", v)} />
      </Section>
    </div>
  );
}

function IssueSource({ name, spec, data, backends, taken, isDefault, startOpen, onChange, onRename, onDefault, onRemove }: {
  name: string; spec: Data; data: Data; backends: any[]; taken: string[]; isDefault: boolean; startOpen?: boolean;
  onChange: (spec: Data) => void; onRename: (to: string) => void; onDefault: () => void; onRemove: () => void;
}) {
  const { sid } = useSession();
  const [open, setOpen] = useState(!!startOpen);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const options: Data = spec.options || {};
  const set = (k: string, v: any) => onChange({ ...spec, [k]: v });
  const ofClass = spec.class ? String(spec.class) : null;
  const classDefs: Data = data.classes || {};
  const classChoices: Choice[] = Object.entries<any>(classDefs).map(([c, d]) => ({
    value: `class:${c}`, description: [`class (backend ${d?.backend || "?"})`, d?.description || d?.label].filter(Boolean).join(": "),
  }));
  const pickBackend = (v: string) => {
    if (v.startsWith("class:")) {                  // a class: the source keeps only its label and the values
      const c = v.slice(6);
      onChange(pruned({ class: c, label: spec.label, vars: ofClass === c ? spec.vars : undefined }));
    } else onChange(ofClass ? pruned({ backend: v, label: spec.label }) : { ...spec, backend: v });
  };
  const where = ["project", "repo", "group", "org"].map((k) => options[k]).find(Boolean);
  const test = async () => {
    setOpen(true);
    setTesting(true);
    setResult(null);
    try {
      setResult(await send("POST", `edit/sessions/${sid}/issues/test`, { source: name, data }));
    } catch (e: any) {
      setResult({ error: String(e?.message || e) });
    } finally {
      setTesting(false);
    }
  };
  return (
    <div className={classes("req-source", isDefault && "default", !open && "folded")}>
      <div className="req-source-head">
        <button className="fold" onClick={() => setOpen(!open)} title={open ? "Fold it" : "Edit it"}>{open ? "▾" : "▸"}</button>
        <IdText value={name} taken={taken} onChange={onRename} />
        {!open && <span className="req-source-sum small muted" onClick={() => setOpen(true)}>
          <code>{spec.backend || "?"}</code>{where ? ` · ${where}` : ""}{spec.label ? ` · ${spec.label}` : ""}</span>}
        <label className="toggle" title="The source of references without a prefix (#42), and of new issues no route picks">
          <input type="radio" checked={isDefault} onChange={onDefault} /> default
        </label>
        <span className="grow" />
        <button className="small" disabled={testing} onClick={test}
          title="Read its open issues as in this form (saved or not): the credentials are read on the sequencer host">
          {testing ? "Testing…" : "Test connection"}</button>
        <button className="icon" title="Remove this source (and its routes)" onClick={onRemove}>×</button>
      </div>
      {open && <>
        {result && (
          <div className={classes("req-test", result.error ? "bad" : "ok")}>
            {result.error ? <div>{result.error}</div> : (
              <div>✓ {result.count} open issue(s) read in {result.seconds} s{result.issues?.length ? ":" : ""}
                {result.issues?.length > 0 && <ul className="small">{result.issues.map((i: any) => <li key={i.id}><code>{name}#{i.id}</code> {i.title}</li>)}</ul>}
                {result.projects?.length > 0 && <div className="small muted">Projects: {result.projects.join(", ")}</div>}
                <IssueTypes types={result.types} />
              </div>
            )}
            {result.calls?.length > 0 && (
              <details className="small"><summary>{result.calls.length} call(s) to the server</summary>
                {result.calls.map((x: any, i: number) => <div key={i} className="mono">{x.method} {x.url} → {x.status ?? x.error}</div>)}
              </details>
            )}
          </div>
        )}
        <Field label="Backend" help="The issue tracker (gitlab, github, jira, redmine, or a plugin's), or a class of this file (class:<name>): a source of a class gives only its label and the values of the class variables."
          error={ofClass ? (classDefs[ofClass] ? null : `no class '${ofClass}' in this file (Classes, below)`) : spec.backend && !backends.some((b) => b.name === String(spec.backend).toLowerCase()) ? `no backend '${spec.backend}' here: a plugin registers it` : null}>
          <Combo mono value={ofClass ? `class:${ofClass}` : spec.backend} onChange={pickBackend} choices={[...classChoices, ...backendChoices(backends)]} />
        </Field>
        {ofClass ? <ClassSourceForm name={name} cls={ofClass} def={classDefs[ofClass]} spec={spec} help={() => undefined} onChange={onChange} /> : <>
          <SpecFields name={name} spec={spec} backends={backends} onChange={onChange} />
          <Field label="Variables" wide help="Values put in its label, options, credentials, labels and filter: {{ name }} (Jinja2). A source of a class gives the values of the class variables instead.">
            <KeyValues value={spec.vars} keyPlaceholder="new variable (team, project…)" placeholder="its value"
              onChange={(v) => set("vars", Object.keys(v).length ? v : undefined)} />
          </Field>
        </>}
      </>}
    </div>
  );
}

/** A glob of a route (fnmatch, case counts) as a regular expression. */
function globRx(glob: string): RegExp {
  let rx = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*") rx += ".*";
    else if (c === "?") rx += ".";
    else if (c === "[") {
      const end = glob.indexOf("]", i + 2);
      if (end < 0) rx += "\\[";
      else { const set = glob.slice(i + 1, end); rx += `[${set.startsWith("!") ? "^" + set.slice(1) : set}]`; i = end; }
    } else rx += c.replace(/[.+^${}()|\\\/]/g, "\\$&");
  }
  return new RegExp(`^${rx}$`, "s");
}

/** The backend part of a source or of a class: label, options, TLS, how it signs in, labels, filter, requirements. */
function SpecFields({ name, spec, backends, onChange }: { name: string; spec: Data; backends: any[]; onChange: (spec: Data) => void }) {
  const backend = backends.find((b) => b.name === String(spec.backend || "").toLowerCase());
  const options: Data = spec.options || {};
  const credentials: Data = spec.credentials || {};
  const set = (k: string, v: any) => onChange({ ...spec, [k]: v });
  const optionChoices: Choice[] = Object.entries<string>(backend?.options || {}).filter(([k]) => k !== "verify")
    .map(([k, d]) => ({ value: k, description: d }));
  // one way to sign in: the method whose credentials are those given (else the first); only its credentials shown
  const methods: any[] = backend?.auth || [];
  const given = Object.keys(credentials).filter((k) => credentials[k]);
  const method = methodOf(methods, given);
  const credRows = method ? [...new Set([...method.credentials, ...given.filter((k) => !method.credentials.includes(k))])]
    : [...new Set([...Object.keys(credentials), ...Object.keys(backend?.credentials || {})])];
  const mixed = !!method && given.some((k) => !method.credentials.includes(k));
  const pickMethod = (name: string) => {
    const m = methods.find((x) => x.name === name);
    if (!m) return;
    const kept = Object.fromEntries(Object.entries(credentials).filter(([k]) => m.credentials.includes(k)));
    set("credentials", Object.keys(kept).length ? kept : undefined);
    setPicked(name);
  };
  const [picked, setPicked] = useState<string | null>(null);
  const shown = picked && !given.length ? methods.find((x) => x.name === picked) || method : method;
  const rows = shown && shown !== method ? shown.credentials : credRows;
  return (
    <>
        <Field label="Label" help="Its name in the UI and the reports (default: the source's name)."><Text value={spec.label} onChange={(v) => set("label", v)} placeholder={name} /></Field>
        <Field label="Options" wide help={"Where the project is, and how to reach it. " + Object.entries<string>(backend?.options || {}).map(([k, d]) => `${k}: ${d}`).join(" ")}
          hint={optionChoices.length > 0 && optionChoices.some((c) => !(c.value in options)) && <span className="small muted">{optionChoices.filter((c) => !(c.value in options)).map((c) => c.value).join(", ")}{optionChoices.some((c) => !(c.value in options)) ? ": other options of this backend" : ""}</span>}>
          <KeyValues value={options} known={optionChoices} exclude={["verify"]} keyPlaceholder="new option (url, project…)" onChange={(v) => set("options", v)} />
        </Field>
        <Field label="TLS" help="How the server's certificate is checked: against the usual authorities; against a CA bundle (a file on the sequencer host, relative to tsq.yml's folder: your company's CA); or not at all (a self-signed certificate: anyone on the way could read the token).">
          <Tls value={options.verify} onChange={(v) => set("options", pruned({ ...options, verify: v }))} />
        </Field>
        {methods.length > 1 && (
          <Field label="Sign in with" help={"The way this source signs in to the server: " + methods.map((m) => `${m.label} (${m.credentials.join(", ")})`).join("; ") + "."}>
            <select value={shown?.name || ""} onChange={(e) => pickMethod(e.target.value)}>
              {methods.map((m) => <option key={m.name} value={m.name}>{m.label}</option>)}
            </select>
          </Field>
        )}
        <Field label="Credentials" wide help={"Never written here: an environment variable (env:) or a file (file:) on the sequencer host. " + Object.entries<string>(backend?.credentials || {}).filter(([k]) => rows.includes(k)).map(([k, d]) => `${k}: ${d}`).join(" ")}>
          <Credentials value={credentials} rows={rows} help={backend?.credentials} onChange={(v) => set("credentials", v)} />
          {mixed && <div className="bad-text small">{given.filter((k) => !shown?.credentials.includes(k)).join(", ")}: not used to sign in this way (remove it)</div>}
        </Field>
        <Field label="Labels" help={"The labels of every issue tsq creates in it (besides those of requirements)." + (backend?.name === "redmine" ? " Redmine has no labels: not sent." : "")}>
          <Tags value={spec.labels} onChange={(v) => set("labels", v.length ? v : undefined)} placeholder={spec.labels?.length ? "" : "tsq"} />
        </Field>
        <Field label="Filter" wide help={FILTER_HELP}>
          <FilterEditor value={spec.filter} onChange={(v) => set("filter", v)} />
        </Field>
        <Field label="Requirements" help={"How the issues of this source name requirements, when not as the file says (its Requirements section, below).\n\n" + REQS_HELP}>
          <ReqRules value={spec.requirements} onChange={(v) => set("requirements", v)} inherit />
        </Field>
    </>
  );
}

function backendChoices(backends: any[]): Choice[] {
  return backends.map((b) => ({ value: b.name, description: [b.description, b.capabilities?.length ? `can: ${b.capabilities.join(", ")}` : ""].filter(Boolean).join(" · ") }));
}

/** The filter of a source or a class: the conditions given, one row each, and Add condition. */
function FilterEditor({ value, onChange }: { value?: Data | null; onChange: (v: Data | undefined) => void }) {
  const f: Data = value && typeof value === "object" ? value : {};
  const given = FILTER_KEYS.filter((k) => f[k.key] !== undefined);
  const missing = FILTER_KEYS.filter((k) => f[k.key] === undefined);
  const put = (k: string, v: any) => {
    const next = { ...f, [k]: v };
    if (v === undefined) delete next[k];
    onChange(Object.keys(next).length ? next : undefined);
  };
  const templ = (x: any) => String(x ?? "").includes("{{");        // a class variable: checked once rendered
  const badDate = (x: any) => !!x && !templ(x) && !DATE.test(String(x).trim());
  const badRx = (x: any) => { if (templ(x)) return null; try { new RegExp(String(x || "")); return null; } catch (e: any) { return String(e.message || e); } };
  return (
    <div className="issues-filter">
      {given.length === 0 && <span className="muted small">none: every issue of the tool. </span>}
      {given.length > 0 && (
        <table className="grid compact"><tbody>{given.map((k) => (
          <tr key={k.key}>
            <td className="nowrap small">{k.label}</td>
            <td>
              {k.kind === "state" && (
                <select value={String(f.state)} onChange={(e) => put("state", e.target.value)}>
                  <option value="open">open</option><option value="closed">closed</option><option value="all">all</option>
                </select>
              )}
              {k.kind === "list" && <Tags value={Array.isArray(f[k.key]) ? f[k.key] : [String(f[k.key])]} placeholder={`add… (${k.placeholder})`}
                onChange={(v) => put(k.key, v.length ? v : [])} />}
              {k.kind === "rx" && <Text mono value={f[k.key]} placeholder={k.placeholder} invalid={!!badRx(f[k.key])} onChange={(v) => put(k.key, v)} />}
              {k.kind === "rx" && badRx(f[k.key]) && <div className="bad-text small">not a regular expression: {badRx(f[k.key])}</div>}
              {k.kind === "date" && <Text mono value={f[k.key]} placeholder={k.placeholder} invalid={badDate(f[k.key])}
                onChange={(v) => put(k.key, v)} />}
              {k.kind === "date" && badDate(f[k.key]) && <div className="bad-text small">a date (2026-01-31) or a time ago (90d, 12w, 6m, 1y)</div>}
            </td>
            <td><button className="icon" title="Remove this condition" onClick={() => put(k.key, undefined)}>×</button></td>
          </tr>
        ))}</tbody></table>
      )}
      {missing.length > 0 && (
        <select value="" onChange={(e) => {
          const k = FILTER_KEYS.find((x) => x.key === e.target.value);
          if (k) put(k.key, k.kind === "state" ? "open" : k.kind === "list" ? [] : "");
        }}>
          <option value="">+ Add a condition…</option>
          {missing.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
        </select>
      )}
    </div>
  );
}

/** The classes of the file: a backend, its options and credentials, labels, filter, written once, with variables;
 *  each source of a class gives only its label and the values of the variables. */
function Classes({ data, backends, setAll }: { data: Data; backends: any[]; setAll: (d: Data) => void }) {
  const defs: Data = data.classes || {};
  const names = Object.keys(defs);
  const [name, setName] = useState("");
  const [added, setAdded] = useState<string | null>(null);
  const bad = name && !NAME.test(name) ? "letters, digits, _ and -" : name in defs ? "already a class" : null;
  const setClass = (n: string, spec: Data | undefined) => {
    const next = clone(data);
    next.classes = { ...(next.classes || {}) };
    if (spec === undefined) delete next.classes[n];
    else next.classes[n] = pruned(spec);
    if (!Object.keys(next.classes).length) delete next.classes;
    setAll(next);
  };
  const create = () => {
    if (!name || bad) return;
    setClass(name, { backend: backends[0]?.name || "gitlab", vars: { project: { mandatory: true, help: "the project of each source" } },
      options: { project: "{{ project }}" } });
    setAdded(name);
    setName("");
  };
  const users = (c: string) => Object.entries<any>(data.sources || {}).filter(([, s]) => String(s?.class || "") === c).map(([n]) => n);
  return (
    <div className="req-sources">
      <div className="req-new">
        <strong>New class</strong>
        <input className="mono" placeholder="its name (company_gitlab…)" value={name} onChange={(e) => setName(e.target.value.trim())}
          onKeyDown={(e) => e.key === "Enter" && create()} />
        <button disabled={!name || !!bad} onClick={create}>+ Create class</button>
        {bad && <span className="bad-text small">{bad}</span>}
      </div>
      {names.map((n) => {
        const def: Data = defs[n] || {};
        const used = users(n);
        return (
          <ClassCard key={n} name={n} def={def} used={used} taken={names} backends={backends} startOpen={added === n}
            onChange={(spec) => setClass(n, spec)}
            onRename={(to) => {
              const next = clone(data);
              next.classes = Object.fromEntries(Object.entries<any>(defs).map(([k, v]) => [k === n ? to : k, v]));
              for (const s of Object.values<any>(next.sources || {})) if (s && s.class === n) s.class = to;
              setAll(next);
            }}
            onRemove={() => setClass(n, undefined)} />
        );
      })}
      {names.length === 0 && <p className="muted small">A class holds what several sources share (the server, how it signs in, a filter), with
        variables each source gives (its project, its team): <code>{"project: \"{{ team }}/bench\""}</code>. A source then picks <code>class:&lt;name&gt;</code> as its backend.</p>}
    </div>
  );
}

function ClassCard({ name, def, used, taken, backends, startOpen, onChange, onRename, onRemove }: {
  name: string; def: Data; used: string[]; taken: string[]; backends: any[]; startOpen?: boolean;
  onChange: (spec: Data) => void; onRename: (to: string) => void; onRemove: () => void;
}) {
  const [open, setOpen] = useState(!!startOpen);
  const vars = Object.keys(def.vars || {});
  return (
    <div className={classes("req-source", !open && "folded")}>
      <div className="req-source-head">
        <button className="fold" onClick={() => setOpen(!open)} title={open ? "Fold it" : "Edit it"}>{open ? "▾" : "▸"}</button>
        <IdText value={name} taken={taken} onChange={onRename} />
        {!open && <span className="req-source-sum small muted" onClick={() => setOpen(true)}>
          <code>{def.backend || "?"}</code>{vars.length ? ` · variables ${vars.join(", ")}` : ""}</span>}
        <span className="grow" />
        <span className="muted small">{used.length ? `used by ${used.join(", ")}` : "no source uses it"}</span>
        <button className="icon" disabled={used.length > 0} title={used.length ? "Its sources use it: give them another backend first" : "Remove this class"} onClick={onRemove}>×</button>
      </div>
      {open && <>
        <Field label="Backend" help="The issue tracker of its sources.">
          <Combo mono value={def.backend} onChange={(v) => onChange({ ...def, backend: v })} choices={backendChoices(backends)} />
        </Field>
        <Field label="Description" help="What it is for (shown when picking a class)."><Text value={def.description} onChange={(v) => onChange({ ...def, description: v || undefined })} /></Field>
        <SpecFields name={name} spec={def} backends={backends} onChange={onChange} />
        <Field label="Class variables" wide help="Declared here, given by each source of the class: {{ name }} in its label, options, credentials, labels and filter. Mandatory: every source must give a value; a default is used when it gives none.">
          <ClassVars value={def.vars || {}} onChange={(v) => onChange({ ...def, vars: Object.keys(v).length ? v : undefined })}
            hint="{{ name }} in the label, options, credentials (env:GITLAB_TOKEN_{{ team }}), labels and filter of the class; each source of it gives the values." />
        </Field>
      </>}
    </div>
  );
}

function Routes({ value, names, defaultName, onChange }: { value: Data[]; names: string[]; defaultName?: string; onChange: (v: Data[]) => void }) {
  const rows = Array.isArray(value) ? value : [];
  const put = (i: number, r: Data) => onChange(rows.map((x, j) => (j === i ? pruned(r) : x)));
  const move = (i: number, d: number) => {
    const next = [...rows];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    onChange(next);
  };
  const sourceChoices: Choice[] = names.map((n) => ({ value: n }));
  // a failure to try the routes on: which source would a new issue about it go to?
  const [sample, setSample] = useState<Data>({ testdef: "", step: "", criteria: "", check: "" });
  const tried = ROUTE_KEYS.some((k) => sample[k.value]);
  const hit = rows.findIndex((r) => ROUTE_KEYS.some((k) => r[k.value])
    && ROUTE_KEYS.every((k) => !r[k.value] || globRx(String(r[k.value])).test(String(sample[k.value] || ""))));
  return (
    <div>
      {rows.length > 0 && (
        <table className="grid compact issues-routes">
          <thead><tr><th>#</th><th title="Where a new issue about a failure this route matches goes">Source</th>
            {ROUTE_KEYS.map((k) => <th key={k.value} title={`${k.description}; e.g. ${k.example}`}>{k.value}</th>)}<th /></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i} className={classes(tried && hit === i && "issues-route-hit")}>
              <td className="muted">{i + 1}</td>
              <td><Combo mono value={r.source} choices={sourceChoices} onChange={(v) => put(i, { ...r, source: v })} /></td>
              {ROUTE_KEYS.map((k) => (
                <td key={k.value} title={`${k.description}: a glob, e.g. ${k.example}; empty: anything`}><Text mono value={r[k.value]} placeholder="*" onChange={(v) => put(i, { ...r, [k.value]: v.trim() || undefined })} /></td>
              ))}
              <td className="nowrap">
                <button className="icon" disabled={i === 0} title="Before" onClick={() => move(i, -1)}>↑</button>
                <button className="icon" disabled={i === rows.length - 1} title="After" onClick={() => move(i, 1)}>↓</button>
                <button className="icon" title="Remove this route" onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
              </td>
            </tr>
          ))}</tbody>
        </table>
      )}
      {rows.some((r) => r.source && !names.includes(r.source)) && <div className="bad-text small">A route names a source that is not in this file.</div>}
      {rows.some((r) => !ROUTE_KEYS.some((k) => r[k.value])) && <div className="bad-text small">A route needs at least one pattern.</div>}
      <button className="small" disabled={!names.length} onClick={() => onChange([...rows, { source: names[0], step: "" }])}>+ Add route</button>
      <div className="hint">Read top to bottom: the first route whose patterns all match a failure picks the source of a new issue about it; none: the
        default source{defaultName ? <> (<code>{defaultName}</code>)</> : null}. Patterns are globs (<code>*</code> any text, <code>?</code> one
        character, <code>[ab]</code> one of them) on the whole value; empty matches anything. E.g. <code>step firmware:*</code> sends the failures of the
        firmware stage to its project; <code>criteria criteria/bench/*</code> the unmet checks of those criteria. A failed verdict has no step,
        criteria nor check: only a <code>testdef</code> pattern takes it. Whoever reports can still pick another source.</div>
      {names.length > 0 && (
        <div className="issues-route-try">
          <span className="small"><strong>Try:</strong> a failure</span>
          {ROUTE_KEYS.map((k) => (
            <input key={k.value} className="mono small" placeholder={k.value} title={k.description} value={sample[k.value]}
              onChange={(e) => setSample({ ...sample, [k.value]: e.target.value })} />
          ))}
          {tried && <span className="small">→ <code>{hit >= 0 ? rows[hit].source : defaultName}</code>
            {hit >= 0 ? ` (route ${hit + 1})` : " (no route matches: the default source)"}</span>}
        </div>
      )}
    </div>
  );
}

function ReqRules({ value, onChange, inherit }: { value?: Data | null; onChange: (v: Data | undefined) => void; inherit?: boolean }) {
  const given: Data = value && typeof value === "object" ? value : {};
  if (inherit && !value) {
    return <span className="small muted">as the file says <button className="link small" onClick={() => onChange({ ...REQ_DEFAULTS })}>set them for this source…</button></span>;
  }
  const v = { ...REQ_DEFAULTS, ...given };
  // written as given: the keys left at their default are left out
  const put = (k: string, x: any) => {
    const next: Data = { ...given, [k]: x };
    for (const [d, dv] of Object.entries(REQ_DEFAULTS)) if (next[d] === dv) delete next[d];
    onChange(Object.keys(next).length || inherit ? next : undefined);
  };
  const labelsOff = v.labels === false;
  const pattern = labelsOff ? "" : String(v.labels);
  return (
    <div className="issues-req-rules">
      <div className="row">
        <Toggle value={!labelsOff} label="Labels:" onChange={(on) => put("labels", on ? REQ_DEFAULTS.labels : false)} />
        {!labelsOff && <Text mono value={pattern} invalid={!pattern.includes("{id}")} onChange={(x) => put("labels", x)} placeholder="req:{id}" />}
        {!labelsOff && !pattern.includes("{id}") && <span className="bad-text small">a pattern with {"{id}"}</span>}
      </div>
      <div className="hint">{labelsOff ? "Labels name no requirement." : <>An issue labelled <code>{pattern.replace("{id}", "REQ-12")}</code> is about REQ-12
        (<code>{pattern.replace("{id}", "jama:SYS-4")}</code>: jama:SYS-4 of another source; spaces become _).</>}</div>
      {!labelsOff && <Toggle value={v.add} label="Give such labels to the issues tsq creates" onChange={(x) => put("add", x)} />}
      {!labelsOff && <div className="hint">One per requirement of the failure, besides the source's labels; editable before the issue is created. Redmine has no labels: none sent.</div>}
      <Toggle value={v.text} label="A known requirement's id in an issue's title or text names it" onChange={(x) => put("text", x)} />
      <div className="hint">As a whole word: “REQ-12 drifts” names REQ-12, “REQ-123” does not. Known requirements: those the test definitions of this repository link (<code>reqs</code>).</div>
      <Toggle value={v.jira_links} label="Jira: an issue link to the requirement's own issue names it" onChange={(x) => put("jira_links", x)} />
      <div className="hint">For requirements kept as Jira issues (their id is the Jira key): an issue linked to one of them (relates to, blocks…) is about it.</div>
      {inherit && <button className="link small" onClick={() => onChange(undefined)}>as the file says</button>}
    </div>
  );
}

/** The sign-in method of the credentials given: the one that has them all (else most of them); the first when none. */
function methodOf(methods: any[], given: string[]): any | null {
  if (!methods.length) return null;
  let best = methods[0];
  let score = -1;
  for (const m of methods) {
    const has = given.filter((k) => m.credentials.includes(k)).length;
    const s = has * 10 - (m.credentials.length - has) - (given.length - has) * 10;
    if (has > 0 && s > score) { best = m; score = s; }
  }
  return best;
}

/** The TLS check of a server: the usual authorities (true, left out), a CA bundle (a file), or none (false). */
function Tls({ value, onChange }: { value: any; onChange: (v: any) => void }) {
  const off = value === false || ["false", "no", "off", "0"].includes(String(value).toLowerCase());
  const bundle = !off && typeof value === "string" && !["true", "yes", "on", "1", ""].includes(value.toLowerCase());
  const [file, setFile] = useState(bundle ? String(value) : "");
  const [choice, setChoice] = useState<"on" | "bundle" | "off" | null>(null);   // a CA bundle picked, its file not given yet
  const mode = off ? "off" : bundle ? "bundle" : choice === "bundle" ? "bundle" : "on";
  return (
    <div className="issues-tls">
      <select value={mode} onChange={(e) => {
        const m = e.target.value as "on" | "bundle" | "off";
        setChoice(m);
        onChange(m === "off" ? false : m === "bundle" ? (file || undefined) : undefined);
      }}>
        <option value="on">Check the certificate</option>
        <option value="bundle">Check it with a CA bundle…</option>
        <option value="off">Don't check it (self-signed)</option>
      </select>
      {mode === "bundle" && <Text mono value={bundle ? String(value) : file} placeholder="certs/company-ca.pem"
        onChange={(v) => { setFile(v); onChange(v.trim() || undefined); }} />}
      {off && <span className="bad-text small">the server is not authenticated: use it only on a network you trust</span>}
    </div>
  );
}
